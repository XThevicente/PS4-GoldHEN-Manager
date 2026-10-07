#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <sys/types.h>

#include <orbis/libkernel.h>
#include <orbis/VideoOut.h>
#include <orbis/Http.h>
#include <orbis/Ssl.h>
#include <orbis/Net.h>
#include <orbis/Sysmodule.h>

#define APP_VERSION "0.2.1"
#define HTTP_SUCCESS 1
#define HTTP_FAILED 0
#define HTTP_USER_AGENT "PS4-GoldHEN-Companion/0.2.1"
#define NET_POOLSIZE (4 * 1024)
#define DISCOVERY_PORT 8786
#define HTTP_PORT 8787
#define OFFER_MAGIC "PS4GH_OFFER_V1|"
#define TOKEN_FILE "/data/ps4gh_companion.token"
#define PAIR_FILE "/data/ps4gh_pair_code.txt"
#define RESULT_FILE "/data/ps4gh_companion_last.json"
#define PC_IP_FILE "/data/ps4gh_pc_ip.txt"
#define TRACE_FILE "/data/ps4gh_companion_boot.log"

#define FB_W 1920
#define FB_H 1080
#define FB_BPP 4
#define FB_COUNT 2
#define VIDEO_MEM_SIZE 0x02000000
#define VIDEO_ALIGN 0x200000

typedef struct {
    uint8_t sin_len;
    uint8_t sin_family;
    uint16_t sin_port;
    OrbisNetInAddr sin_addr;
    char sin_zero[8];
} OrbisNetSockaddrInCompat;

static int libnetMemId = 0, libhttpCtxId = 0, libsslCtxId = 0;

static int video = -1;
static OrbisKernelEqueue flipQueue;
static OrbisVideoOutBufferAttribute fbAttr;
static char *frameBuffers[FB_COUNT] = {0};
static int activeFb = 0;
static int frameId = 1;
static off_t directMemOff = 0;
static size_t directMemSize = 0;
static void *videoMem = NULL;

static const char FONT_CHARS[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:/_?";
static const uint8_t FONT[][7] = {
    {0,0,0,0,0,0,0},
    {14,17,17,31,17,17,17},{30,17,17,30,17,17,30},{14,17,16,16,16,17,14},
    {30,17,17,17,17,17,30},{31,16,16,30,16,16,31},{31,16,16,30,16,16,16},
    {14,17,16,23,17,17,15},{17,17,17,31,17,17,17},{14,4,4,4,4,4,14},
    {7,2,2,2,18,18,12},{17,18,20,24,20,18,17},{16,16,16,16,16,16,31},
    {17,27,21,21,17,17,17},{17,25,21,19,17,17,17},{14,17,17,17,17,17,14},
    {30,17,17,30,16,16,16},{14,17,17,17,21,18,13},{30,17,17,30,20,18,17},
    {15,16,16,14,1,1,30},{31,4,4,4,4,4,4},{17,17,17,17,17,17,14},
    {17,17,17,17,17,10,4},{17,17,17,21,21,21,10},{17,17,10,4,10,17,17},
    {17,17,10,4,4,4,4},{31,1,2,4,8,16,31},
    {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},{14,17,1,2,4,8,31},
    {30,1,1,14,1,1,30},{2,6,10,18,31,2,2},{31,16,30,1,1,17,14},
    {6,8,16,30,17,17,14},{31,1,2,4,8,8,8},{14,17,17,14,17,17,14},
    {14,17,17,15,1,2,12},
    {0,0,0,31,0,0,0},{0,0,0,0,0,12,12},{0,1,2,4,8,16,0},{0,12,12,0,12,12,0},
    {0,0,0,0,0,0,31},{14,17,1,2,4,0,4}
};

static uint32_t rgb(uint8_t r, uint8_t g, uint8_t b) {
    return 0x80000000u | ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
}

static void trace_marker(const char *marker) {
    printf("[PS4GH] %s\n", marker);
    FILE *f = fopen(TRACE_FILE, "ab");
    if (f) {
        fprintf(f, "%s\n", marker);
        fclose(f);
    }
}

static void put_pixel(int x, int y, uint32_t c) {
    if (x < 0 || y < 0 || x >= FB_W || y >= FB_H || !frameBuffers[activeFb]) return;
    ((uint32_t *)frameBuffers[activeFb])[y * FB_W + x] = c;
}

static void fill_rect(int x, int y, int w, int h, uint32_t c) {
    int x2 = x + w, y2 = y + h;
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (x2 > FB_W) x2 = FB_W;
    if (y2 > FB_H) y2 = FB_H;
    for (int yy = y; yy < y2; ++yy) {
        uint32_t *row = &((uint32_t *)frameBuffers[activeFb])[yy * FB_W];
        for (int xx = x; xx < x2; ++xx) row[xx] = c;
    }
}

static int font_index(char ch) {
    if (ch >= 'a' && ch <= 'z') ch = (char)(ch - 32);
    const char *p = strchr(FONT_CHARS, ch);
    if (!p) p = strchr(FONT_CHARS, '?');
    return (int)(p - FONT_CHARS);
}

static void draw_char(int x, int y, char ch, int scale, uint32_t c) {
    int idx = font_index(ch);
    for (int row = 0; row < 7; ++row) {
        uint8_t bits = FONT[idx][row];
        for (int col = 0; col < 5; ++col) {
            if (bits & (1u << (4 - col))) {
                fill_rect(x + col * scale, y + row * scale, scale, scale, c);
            }
        }
    }
}

static void draw_text(int x, int y, const char *s, int scale, uint32_t c) {
    int ox = x;
    while (*s) {
        if (*s == '\n') {
            y += 9 * scale;
            x = ox;
        } else {
            draw_char(x, y, *s, scale, c);
            x += 6 * scale;
        }
        ++s;
    }
}

static void wait_flip(int id) {
    OrbisKernelEvent evt;
    int count = 0;
    for (;;) {
        OrbisVideoOutFlipStatus st;
        memset(&st, 0, sizeof(st));
        sceVideoOutGetFlipStatus(video, &st);
        if (st.flipArg == id) break;
        if (sceKernelWaitEqueue(flipQueue, &evt, 1, &count, 0) != 0) break;
    }
}

static void present(void) {
    sceVideoOutSubmitFlip(video, activeFb, ORBIS_VIDEO_OUT_FLIP_VSYNC, frameId);
    wait_flip(frameId);
    frameId++;
    activeFb = (activeFb + 1) % FB_COUNT;
}

static int video_init(void) {
    video = sceVideoOutOpen(ORBIS_VIDEO_USER_MAIN, ORBIS_VIDEO_OUT_BUS_MAIN, 0, 0);
    if (video < 0) return 0;
    if (sceKernelCreateEqueue(&flipQueue, "ps4gh_flip") < 0) return 0;
    if (sceVideoOutAddFlipEvent(flipQueue, video, 0) < 0) return 0;

    directMemSize = (VIDEO_MEM_SIZE + VIDEO_ALIGN - 1) / VIDEO_ALIGN * VIDEO_ALIGN;
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), directMemSize,
                                      VIDEO_ALIGN, 3, &directMemOff) < 0) return 0;
    if (sceKernelMapDirectMemory(&videoMem, directMemSize, 0x33, 0,
                                 directMemOff, VIDEO_ALIGN) < 0) return 0;

    size_t fbSize = FB_W * FB_H * FB_BPP;
    uintptr_t base = (uintptr_t)videoMem;
    for (int i = 0; i < FB_COUNT; ++i) frameBuffers[i] = (char *)(base + (fbSize * i));

    sceVideoOutSetBufferAttribute(&fbAttr, 0x80000000, 1, 0, FB_W, FB_H, FB_W);
    if (sceVideoOutRegisterBuffers(video, 0, (void **)frameBuffers, FB_COUNT, &fbAttr) != 0) return 0;
    sceVideoOutSetFlipRate(video, 0);
    return 1;
}

static void ui_screen(const char *status, const char *detail, int good) {
    const uint32_t bg = rgb(5, 16, 34);
    const uint32_t panel = rgb(11, 31, 59);
    const uint32_t blue = rgb(38, 181, 255);
    const uint32_t gold = rgb(245, 185, 52);
    const uint32_t white = rgb(236, 245, 255);
    const uint32_t muted = rgb(141, 164, 190);
    const uint32_t ok = rgb(60, 210, 120);
    const uint32_t bad = rgb(255, 92, 92);

    fill_rect(0, 0, FB_W, FB_H, bg);
    fill_rect(0, 0, FB_W, 18, blue);
    fill_rect(105, 120, 1710, 830, panel);

    draw_text(160, 175, "PS4 GOLDHEN", 6, blue);
    draw_text(160, 235, "COMPANION", 6, gold);
    draw_text(160, 320, "V0.2", 4, muted);

    fill_rect(160, 410, 28, 28, good ? ok : bad);
    draw_text(215, 402, status ? status : "INICIALIZANDO", 5, white);

    if (detail && detail[0]) {
        draw_text(215, 490, detail, 4, muted);
    }

    draw_text(160, 810, "PS4 <-> PC  LAN ONLY", 3, muted);
    draw_text(160, 860, "UDP 8786   HTTP 8787", 3, muted);

    present();

    /* Keep both buffers visually in sync, so a later flip never reveals old/black content. */
    fill_rect(0, 0, FB_W, FB_H, bg);
    fill_rect(0, 0, FB_W, 18, blue);
    fill_rect(105, 120, 1710, 830, panel);
    draw_text(160, 175, "PS4 GOLDHEN", 6, blue);
    draw_text(160, 235, "COMPANION", 6, gold);
    draw_text(160, 320, "V0.2", 4, muted);
    fill_rect(160, 410, 28, 28, good ? ok : bad);
    draw_text(215, 402, status ? status : "INICIALIZANDO", 5, white);
    if (detail && detail[0]) draw_text(215, 490, detail, 4, muted);
    draw_text(160, 810, "PS4 <-> PC  LAN ONLY", 3, muted);
    draw_text(160, 860, "UDP 8786   HTTP 8787", 3, muted);
    present();
}

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
    if (s < 0) return 0;

    OrbisNetSockaddrInCompat bind_addr;
    memset(&bind_addr, 0, sizeof(bind_addr));
    bind_addr.sin_len = sizeof(bind_addr);
    bind_addr.sin_family = ORBIS_NET_AF_INET;
    bind_addr.sin_port = sceNetHtons(DISCOVERY_PORT);
    bind_addr.sin_addr.s_addr = 0;

    int ret = sceNetBind(s, (const OrbisNetSockaddr *)&bind_addr, sizeof(bind_addr));
    if (ret < 0) {
        sceNetSocketClose(s);
        return 0;
    }

    char buf[512];
    OrbisNetSockaddr from;
    OrbisNetSocklen_t from_len = sizeof(from);
    ret = sceNetRecvfrom(s, buf, sizeof(buf) - 1, 0, &from, &from_len);
    sceNetSocketClose(s);
    if (ret <= 0) return 0;
    buf[ret] = 0;

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
    setvbuf(stdout, NULL, _IONBF, 0);

    trace_marker("BOOT_01 START");
    if (!video_init()) {
        trace_marker("VIDEO_02 FAIL");
        for (;;) {}
    }

    trace_marker("VIDEO_02 OK");
    ui_screen("INICIALIZANDO", "VIDEO OUT OK", 1);

    trace_marker("NET_03 START");
    if (!net_http_init()) {
        trace_marker("NET_03 FAIL");
        ui_screen("ERROR DE RED", "NO SE PUDO INICIALIZAR NET HTTP", 0);
        for (;;) {}
    }

    trace_marker("NET_03 OK");
    ui_screen("BUSCANDO PC", "INICIA COMPANION SERVER EN WINDOWS", 1);

    char pc_ip[64] = {0};
    int pc_port = HTTP_PORT;

    if (read_text_file(PC_IP_FILE, pc_ip, sizeof(pc_ip))) {
        trace_marker("DISCOVERY_04 MANUAL IP");
        char manual_detail[256];
        snprintf(manual_detail, sizeof(manual_detail), "IP MANUAL %s:%d", pc_ip, pc_port);
        ui_screen("PC CONFIGURADO", manual_detail, 1);
    } else if (!discover_pc(pc_ip, sizeof(pc_ip), &pc_port)) {
        trace_marker("DISCOVERY_04 FAIL");
        ui_screen("PC NO ENCONTRADO", "REVISA FIREWALL O CREA /DATA/PS4GH_PC_IP.TXT", 0);
        net_http_end();
        for (;;) {}
    } else {
        trace_marker("DISCOVERY_04 OK");
    }
    char detail[256];
    snprintf(detail, sizeof(detail), "PC %s:%d", pc_ip, pc_port);
    ui_screen("PC ENCONTRADO", detail, 1);

    char base[128];
    snprintf(base, sizeof(base), "http://%s:%d", pc_ip, pc_port);

    char response[4096];
    char url[512];
    int status = 0;
    snprintf(url, sizeof(url), "%s/api/v1/ping", base);
    if (!http_get_text(url, NULL, response, sizeof(response), &status) || status != 200) {
        trace_marker("PING_05 FAIL");
        snprintf(detail, sizeof(detail), "HTTP %d", status);
        ui_screen("FALLO PING", detail, 0);
        net_http_end();
        for (;;) {}
    }

    trace_marker("PING_05 OK");
    write_text_file(RESULT_FILE, response);

    char token[256] = {0};
    if (!read_text_file(TOKEN_FILE, token, sizeof(token))) {
        trace_marker("PAIR_06 NEED CODE");
        char pair_code[32] = {0};
        if (!read_text_file(PAIR_FILE, pair_code, sizeof(pair_code))) {
            ui_screen("SIN EMPAREJAR", "CREA /DATA/PS4GH_PAIR_CODE.TXT", 0);
            net_http_end();
            for (;;) {}
        }

        ui_screen("EMPAREJANDO", "VALIDANDO CODIGO CON EL PC", 1);
        snprintf(url, sizeof(url), "%s/api/v1/pair?code=%s&device=PS4", base, pair_code);
        if (!http_get_text(url, NULL, response, sizeof(response), &status) || status != 200 ||
            !json_extract_string(response, "token", token, sizeof(token))) {
            trace_marker("PAIR_06 FAIL");
            snprintf(detail, sizeof(detail), "HTTP %d  REVISA EL CODIGO", status);
            ui_screen("FALLO EMPAREJADO", detail, 0);
            net_http_end();
            for (;;) {}
        }

        write_text_file(TOKEN_FILE, token);
        trace_marker("PAIR_06 OK TOKEN SAVED");
    } else {
        trace_marker("PAIR_06 TOKEN FOUND");
    }

    ui_screen("TOKEN OK", "COMPROBANDO SESION AUTENTICADA", 1);

    snprintf(url, sizeof(url), "%s/api/v1/status", base);
    if (http_get_text(url, token, response, sizeof(response), &status) && status == 200) {
        trace_marker("STATUS_07 CONNECTED");
        write_text_file(RESULT_FILE, response);
        snprintf(detail, sizeof(detail), "PC %s:%d  TOKEN OK", pc_ip, pc_port);
        ui_screen("CONECTADO", detail, 1);
    } else {
        trace_marker("STATUS_07 FAIL");
        snprintf(detail, sizeof(detail), "HTTP %d  BORRA TOKEN Y EMPAREJA", status);
        ui_screen("TOKEN NO VALIDO", detail, 0);
    }

    net_http_end();
    trace_marker("DONE_08");
    for (;;) {}
}
