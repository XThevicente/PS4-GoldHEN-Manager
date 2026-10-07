#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>

#include <orbis/Http.h>
#include <orbis/Ssl.h>
#include <orbis/Net.h>
#include <orbis/Sysmodule.h>

#define HTTP_SUCCESS 1
#define HTTP_FAILED 0
#define HTTP_USER_AGENT "PS4-GoldHEN-Companion/0.1"
#define NET_POOLSIZE (4 * 1024)
#define DISCOVERY_PORT 8786
#define HTTP_PORT 8787
#define OFFER_MAGIC "PS4GH_OFFER_V1|"
#define TOKEN_FILE "/data/ps4gh_companion.token"
#define PAIR_FILE "/data/ps4gh_pair_code.txt"
#define RESULT_FILE "/data/ps4gh_companion_last.json"

typedef struct OrbisNetSockaddrInCompat {
    uint8_t sin_len;
    uint8_t sin_family;
    uint16_t sin_port;
    OrbisNetInAddr sin_addr;
    char sin_zero[8];
} OrbisNetSockaddrInCompat;

static int libnetMemId = 0, libhttpCtxId = 0, libsslCtxId = 0;

static int net_http_init(void) {
    int ret;
    if (sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_NET) < 0) return HTTP_FAILED;
    if (sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_HTTP) < 0) return HTTP_FAILED;
    if (sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_SSL) < 0) return HTTP_FAILED;

    ret = sceNetInit();
    (void)ret;
    ret = sceNetPoolCreate("PS4GHNetPool", NET_POOLSIZE, 0);
    if (ret < 0) return HTTP_FAILED;
    libnetMemId = ret;

    ret = sceSslInit(SSL_POOLSIZE);
    if (ret < 0) return HTTP_FAILED;
    libsslCtxId = ret;

    ret = sceHttpInit(libnetMemId, libsslCtxId, LIBHTTP_POOLSIZE);
    if (ret < 0) return HTTP_FAILED;
    libhttpCtxId = ret;
    return HTTP_SUCCESS;
}

static void net_http_end(void) {
    if (libhttpCtxId > 0) sceHttpTerm(libhttpCtxId);
    if (libsslCtxId > 0) sceSslTerm(libsslCtxId);
    if (libnetMemId > 0) sceNetPoolDestroy(libnetMemId);
}

static int read_text_file(const char *path, char *out, size_t cap) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    size_t n = fread(out, 1, cap - 1, f);
    fclose(f);
    out[n] = 0;
    while (n && (out[n-1] == '\r' || out[n-1] == '\n' || out[n-1] == ' ' || out[n-1] == '\t')) out[--n] = 0;
    return n > 0;
}

static int write_text_file(const char *path, const char *text) {
    FILE *f = fopen(path, "wb");
    if (!f) return 0;
    size_t len = strlen(text);
    size_t wr = fwrite(text, 1, len, f);
    fclose(f);
    return wr == len;
}

static int discover_pc(char *ip_out, size_t ip_cap, int *port_out) {
    OrbisNetId s = sceNetSocket("PS4GHDiscovery", ORBIS_NET_AF_INET, ORBIS_NET_SOCK_DGRAM, 0);
    if (s < 0) {
        printf("discovery: sceNetSocket failed 0x%08X\n", s);
        return 0;
    }

    OrbisNetSockaddrInCompat bind_addr;
    memset(&bind_addr, 0, sizeof(bind_addr));
    bind_addr.sin_len = sizeof(bind_addr);
    bind_addr.sin_family = ORBIS_NET_AF_INET;
    bind_addr.sin_port = sceNetHtons(DISCOVERY_PORT);
    bind_addr.sin_addr.s_addr = 0;

    int ret = sceNetBind(s, (const OrbisNetSockaddr *)&bind_addr, sizeof(bind_addr));
    if (ret < 0) {
        printf("discovery: sceNetBind failed 0x%08X\n", ret);
        sceNetSocketClose(s);
        return 0;
    }

    printf("Waiting for PC companion broadcast on UDP %d...\n", DISCOVERY_PORT);
    char buf[512];
    OrbisNetSockaddr from;
    OrbisNetSocklen_t from_len = sizeof(from);
    ret = sceNetRecvfrom(s, buf, sizeof(buf) - 1, 0, &from, &from_len);
    sceNetSocketClose(s);
    if (ret <= 0) {
        printf("discovery: receive failed 0x%08X\n", ret);
        return 0;
    }
    buf[ret] = 0;
    printf("Discovery packet: %s\n", buf);

    if (strncmp(buf, OFFER_MAGIC, strlen(OFFER_MAGIC)) != 0) return 0;
    char *p = buf + strlen(OFFER_MAGIC);
    char *sep = strchr(p, '|');
    if (!sep) return 0;
    *sep = 0;
    strncpy(ip_out, p, ip_cap - 1);
    ip_out[ip_cap - 1] = 0;

    char *port = sep + 1;
    sep = strchr(port, '|');
    if (sep) *sep = 0;
    *port_out = atoi(port);
    if (*port_out <= 0 || *port_out > 65535) *port_out = HTTP_PORT;
    return 1;
}

static int http_get_text(const char *url, const char *token, char *out, size_t out_cap, int *status_out) {
    int ret, tpl = 0, conn = 0, req = 0;
    size_t used = 0;
    if (status_out) *status_out = 0;

    tpl = sceHttpCreateTemplate(libhttpCtxId, HTTP_USER_AGENT, ORBIS_HTTP_VERSION_1_1, 1);
    if (tpl < 0) goto fail;
    conn = sceHttpCreateConnectionWithURL(tpl, url, 1);
    if (conn < 0) goto fail;
    req = sceHttpCreateRequestWithURL(conn, ORBIS_METHOD_GET, url, 0);
    if (req < 0) goto fail;

    if (token && token[0]) {
        char auth[256];
        snprintf(auth, sizeof(auth), "Bearer %s", token);
        sceHttpAddRequestHeader(req, "Authorization", auth, 0);
    }

    ret = sceHttpSetConnectTimeOut(req, 3000000);
    (void)ret;
    sceHttpSetRecvTimeOut(req, 3000000);

    ret = sceHttpSendRequest(req, NULL, 0);
    if (ret < 0) goto fail;

    int32_t status = 0;
    if (sceHttpGetStatusCode(req, &status) < 0) goto fail;
    if (status_out) *status_out = status;

    while (used + 1 < out_cap) {
        int n = sceHttpReadData(req, out + used, (uint32_t)(out_cap - used - 1));
        if (n < 0) goto fail;
        if (n == 0) break;
        used += (size_t)n;
    }
    out[used] = 0;

    if (req > 0) sceHttpDeleteRequest(req);
    if (conn > 0) sceHttpDeleteConnection(conn);
    if (tpl > 0) sceHttpDeleteTemplate(tpl);
    return 1;

fail:
    if (req > 0) sceHttpDeleteRequest(req);
    if (conn > 0) sceHttpDeleteConnection(conn);
    if (tpl > 0) sceHttpDeleteTemplate(tpl);
    return 0;
}

static int json_extract_string(const char *json, const char *key, char *out, size_t cap) {
    char needle[64];
    snprintf(needle, sizeof(needle), "\"%s\":\"", key);
    const char *p = strstr(json, needle);
    if (!p) return 0;
    p += strlen(needle);
    const char *e = strchr(p, '"');
    if (!e) return 0;
    size_t n = (size_t)(e - p);
    if (n >= cap) n = cap - 1;
    memcpy(out, p, n);
    out[n] = 0;
    return 1;
}

int main(void) {
    printf("PS4 GoldHEN Companion v0.1 starting...\n");
    if (!net_http_init()) {
        printf("Network/HTTP initialization failed.\n");
        for (;;) {}
    }

    char pc_ip[64] = {0};
    int pc_port = HTTP_PORT;
    if (!discover_pc(pc_ip, sizeof(pc_ip), &pc_port)) {
        printf("PC companion not found. Start companion_server.py on the PC.\n");
        net_http_end();
        for (;;) {}
    }

    char base[128];
    snprintf(base, sizeof(base), "http://%s:%d", pc_ip, pc_port);
    printf("PC companion discovered at %s\n", base);

    char response[4096];
    char url[512];
    int status = 0;
    snprintf(url, sizeof(url), "%s/api/v1/ping", base);
    if (!http_get_text(url, NULL, response, sizeof(response), &status) || status != 200) {
        printf("Ping failed (HTTP %d)\n", status);
        net_http_end();
        for (;;) {}
    }
    printf("Ping OK: %s\n", response);
    write_text_file(RESULT_FILE, response);

    char token[256] = {0};
    if (!read_text_file(TOKEN_FILE, token, sizeof(token))) {
        char pair_code[32] = {0};
        if (!read_text_file(PAIR_FILE, pair_code, sizeof(pair_code))) {
            printf("Not paired. Put the 6-digit PC pair code in %s and restart.\n", PAIR_FILE);
            net_http_end();
            for (;;) {}
        }
        snprintf(url, sizeof(url), "%s/api/v1/pair?code=%s&device=PS4", base, pair_code);
        if (!http_get_text(url, NULL, response, sizeof(response), &status) || status != 200 ||
            !json_extract_string(response, "token", token, sizeof(token))) {
            printf("Pairing failed (HTTP %d): %s\n", status, response);
            net_http_end();
            for (;;) {}
        }
        write_text_file(TOKEN_FILE, token);
        printf("Pairing OK. Token saved to %s\n", TOKEN_FILE);
    }

    snprintf(url, sizeof(url), "%s/api/v1/status", base);
    if (http_get_text(url, token, response, sizeof(response), &status) && status == 200) {
        printf("COMPANION CONNECTED: %s\n", response);
        write_text_file(RESULT_FILE, response);
    } else {
        printf("Authenticated status failed (HTTP %d). Delete %s and pair again if needed.\n", status, TOKEN_FILE);
    }

    net_http_end();
    printf("Done. Result written to %s\n", RESULT_FILE);
    for (;;) {}
}
