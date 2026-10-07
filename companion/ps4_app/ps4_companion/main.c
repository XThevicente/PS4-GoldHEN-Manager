#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <sys/types.h>
#include <time.h>

#include <orbis/libkernel.h>
#include <orbis/VideoOut.h>
#include <orbis/Http.h>
#include <orbis/Ssl.h>
#include <orbis/Net.h>
#include <orbis/Sysmodule.h>
#include <orbis/UserService.h>
#include <orbis/Pad.h>

#define APP_VERSION "0.5.0"
#define HTTP_SUCCESS 1
#define HTTP_FAILED 0
#define HTTP_USER_AGENT "PS4-GoldHEN-Companion/0.5.0"
#define NET_POOLSIZE (4 * 1024)
#define DISCOVERY_PORT 8786
#define HTTP_PORT 8787
#define OFFER_MAGIC "PS4GH_OFFER_V1|"
#define TOKEN_FILE "/data/ps4gh_companion.token"
#define PAIR_FILE "/data/ps4gh_pair_code.txt"
#define RESULT_FILE "/data/ps4gh_companion_last.json"
#define PC_IP_FILE "/data/ps4gh_pc_ip.txt"
#define RECEIVED_FILE "/data/ps4gh_received.bin"
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
static int netCoreStarted = 0;

static int video = -1;
static OrbisKernelEqueue flipQueue;
static OrbisVideoOutBufferAttribute fbAttr;
static char *frameBuffers[FB_COUNT] = {0};
static int activeFb = 0;
static int frameId = 1;
static off_t directMemOff = 0;
static size_t directMemSize = 0;
static void *videoMem = NULL;

static int padHandle = -1;

static const char FONT_CHARS[] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-./:_?";
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

static void fill_rect(int x, int y, int w, int h, uint32_t c) {
    if (!frameBuffers[activeFb]) return;
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

static void draw_base(void) {
    const uint32_t bg = rgb(5, 16, 34);
    const uint32_t panel = rgb(11, 31, 59);
    const uint32_t blue = rgb(38, 181, 255);
    const uint32_t gold = rgb(245, 185, 52);
    const uint32_t muted = rgb(141, 164, 190);

    fill_rect(0, 0, FB_W, FB_H, bg);
    fill_rect(0, 0, FB_W, 18, blue);
    fill_rect(105, 120, 1710, 830, panel);
    draw_text(160, 175, "PS4 GOLDHEN", 6, blue);
    draw_text(160, 235, "COMPANION", 6, gold);
    draw_text(160, 320, "V0.5", 4, muted);
    draw_text(160, 810, "OPTIONS MENU   PS4 TO PC  LAN ONLY", 3, muted);
    draw_text(160, 860, "UDP 8786   HTTP 8787", 3, muted);
}

static void draw_status_frame(const char *status, const char *detail, int good) {
    const uint32_t white = rgb(236, 245, 255);
    const uint32_t muted = rgb(141, 164, 190);
    const uint32_t ok = rgb(60, 210, 120);
    const uint32_t bad = rgb(255, 92, 92);

    draw_base();
    fill_rect(160, 410, 28, 28, good ? ok : bad);
    draw_text(215, 402, status ? status : "INICIALIZANDO", 5, white);
    if (detail && detail[0]) draw_text(215, 490, detail, 4, muted);
}

static void ui_screen(const char *status, const char *detail, int good) {
    for (int i = 0; i < 2; ++i) {
        draw_status_frame(status, detail, good);
        present();
    }
}

static void draw_pair_frame(const char code[7], int selected) {
    const uint32_t white = rgb(236, 245, 255);
    const uint32_t muted = rgb(141, 164, 190);
    const uint32_t blue = rgb(38, 181, 255);
    const uint32_t gold = rgb(245, 185, 52);
    const uint32_t dark = rgb(5, 16, 34);

    draw_base();
    draw_text(160, 400, "EMPAREJAR CON PC", 5, white);
    draw_text(160, 475, "INTRODUCE EL CODIGO DEL PC", 3, muted);

    int startX = 250;
    int y = 565;
    for (int i = 0; i < 6; ++i) {
        int x = startX + i * 120;
        if (i == selected) {
            fill_rect(x - 15, y - 15, 85, 110, gold);
            draw_char(x, y, code[i], 8, dark);
        } else {
            fill_rect(x - 15, y - 15, 85, 110, blue);
            draw_char(x, y, code[i], 8, white);
        }
    }

    draw_text(160, 715, "D-PAD CAMBIA Y MUEVE   X ACEPTA", 3, muted);
}

static void ui_pair_code(const char code[7], int selected) {
    for (int i = 0; i < 2; ++i) {
        draw_pair_frame(code, selected);
        present();
    }
}

static void draw_menu_frame(int selected) {
    const char *items[] = {"ESTADO", "INFO CONSOLA", "RETRO MANAGER", "RECONECTAR HTTP", "VOLVER"};
    const uint32_t white = rgb(236, 245, 255);
    const uint32_t muted = rgb(141, 164, 190);
    const uint32_t blue = rgb(38, 181, 255);
    const uint32_t gold = rgb(245, 185, 52);
    const uint32_t dark = rgb(5, 16, 34);

    draw_base();
    draw_text(160, 390, "MENU LOCAL", 5, white);
    for (int i = 0; i < 5; ++i) {
        int y = 460 + i * 60;
        if (i == selected) {
            fill_rect(160, y - 10, 620, 55, gold);
            draw_text(180, y, items[i], 4, dark);
        } else {
            fill_rect(160, y - 10, 620, 55, blue);
            draw_text(180, y, items[i], 4, white);
        }
    }
    draw_text(850, 500, "ARRIBA/ABAJO SELECCIONA", 3, muted);
    draw_text(850, 550, "X ACEPTA   O VOLVER", 3, muted);
}

static void ui_menu(int selected) {
    for (int i = 0; i < 2; ++i) {
        draw_menu_frame(selected);
        present();
    }
}

static void get_firmware(char *out, size_t cap) {
    OrbisKernelSwVersion sw;
    memset(&sw, 0, sizeof(sw));
    sw.Size = sizeof(sw);
    if (sceKernelGetSystemSwVersion(&sw) == 0 && sw.VersionString[0]) {
        size_t n = 0;
        for (size_t i = 0; i < sizeof(sw.VersionString) && sw.VersionString[i] && n + 1 < cap; ++i) {
            char ch = sw.VersionString[i];
            if ((ch >= '0' && ch <= '9') || ch == '.') out[n++] = ch;
        }
        out[n] = 0;
        if (n) return;
    }
    snprintf(out, cap, "DESCONOCIDA");
}

static int pad_init(void) {
    sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_USER_SERVICE);
    sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_PAD);

    if (scePadInit() != 0) return 0;

    OrbisUserServiceInitializeParams param;
    memset(&param, 0, sizeof(param));
    param.priority = ORBIS_KERNEL_PRIO_FIFO_LOWEST;
    sceUserServiceInitialize(&param);

    int userId = -1;
    if (sceUserServiceGetInitialUser(&userId) != 0) return 0;

    padHandle = scePadOpen(userId, ORBIS_PAD_PORT_TYPE_STANDARD, 0, NULL);
    return padHandle >= 0;
}

static uint32_t pad_navigation(const OrbisPadData *data) {
    uint32_t buttons = data->buttons;
    if (data->connected) {
        if (data->leftStick.x < 64) buttons |= ORBIS_PAD_BUTTON_LEFT;
        if (data->leftStick.x > 192) buttons |= ORBIS_PAD_BUTTON_RIGHT;
        if (data->leftStick.y < 64) buttons |= ORBIS_PAD_BUTTON_UP;
        if (data->leftStick.y > 192) buttons |= ORBIS_PAD_BUTTON_DOWN;
    }
    return buttons;
}

static int input_pair_code(char out[7]) {
    if (padHandle < 0) return 0;

    strcpy(out, "000000");
    int selected = 0;
    uint32_t prev = 0;
    ui_pair_code(out, selected);

    for (;;) {
        OrbisPadData data;
        memset(&data, 0, sizeof(data));
        if (scePadReadState(padHandle, &data) < 0) {
            sceKernelUsleep(30000);
            continue;
        }

        uint32_t now = pad_navigation(&data);
        uint32_t pressed = now & ~prev;
        prev = now;
        int changed = 0;

        if (pressed & ORBIS_PAD_BUTTON_LEFT) {
            selected = (selected + 5) % 6;
            changed = 1;
        }
        if (pressed & ORBIS_PAD_BUTTON_RIGHT) {
            selected = (selected + 1) % 6;
            changed = 1;
        }
        if (pressed & ORBIS_PAD_BUTTON_UP) {
            out[selected] = (out[selected] == '9') ? '0' : (char)(out[selected] + 1);
            changed = 1;
        }
        if (pressed & ORBIS_PAD_BUTTON_DOWN) {
            out[selected] = (out[selected] == '0') ? '9' : (char)(out[selected] - 1);
            changed = 1;
        }
        if (pressed & ORBIS_PAD_BUTTON_CROSS) return 1;

        if (changed) ui_pair_code(out, selected);
        sceKernelUsleep(30000);
    }
}

static void pad_feedback(void) {
    if (padHandle < 0) return;
    OrbisPadVibeParam vibe;
    vibe.lgMotor = 120;
    vibe.smMotor = 80;
    scePadSetVibration(padHandle, &vibe);
    sceKernelUsleep(120000);
    vibe.lgMotor = 0;
    vibe.smMotor = 0;
    scePadSetVibration(padHandle, &vibe);
}

static void retro_loop(const char *pc_ip, int pc_port);
static void menu_heartbeat(const char *pc_ip, int pc_port);

static int menu_loop(const char *pc_ip, int pc_port) {
    if (padHandle < 0) return 0;
    int selected = 0;
    uint32_t prev = 0;
    ui_menu(selected);
    unsigned menuTicks = 0;

    /* Wait for OPTIONS to be released before accepting menu input. */
    for (int i = 0; i < 12; ++i) {
        OrbisPadData d;
        memset(&d, 0, sizeof(d));
        if (scePadReadState(padHandle, &d) >= 0) prev = d.buttons;
        if (!(prev & ORBIS_PAD_BUTTON_OPTIONS)) break;
        sceKernelUsleep(30000);
    }

    for (;;) {
        OrbisPadData data;
        memset(&data, 0, sizeof(data));
        if (scePadReadState(padHandle, &data) < 0) {
            sceKernelUsleep(30000);
            continue;
        }
        uint32_t now = pad_navigation(&data);
        uint32_t pressed = now & ~prev;
        prev = now;
        int changed = 0;

        if (pressed & ORBIS_PAD_BUTTON_UP) {
            selected = (selected + 4) % 5;
            changed = 1;
        }
        if (pressed & ORBIS_PAD_BUTTON_DOWN) {
            selected = (selected + 1) % 5;
            changed = 1;
        }
        if (pressed & ORBIS_PAD_BUTTON_CIRCLE) return 0;

        if (pressed & ORBIS_PAD_BUTTON_CROSS) {
            char detail[192];
            if (selected == 0) {
                snprintf(detail, sizeof(detail), "PC %s:%d  ENLACE ACTIVO", pc_ip, pc_port);
                ui_screen("ESTADO", detail, 1);
                sceKernelUsleep(1100000);
                ui_menu(selected);
            } else if (selected == 1) {
                char fw[64];
                get_firmware(fw, sizeof(fw));
                snprintf(detail, sizeof(detail), "FIRMWARE %s   APP %s", fw, APP_VERSION);
                ui_screen("INFO CONSOLA", detail, 1);
                sceKernelUsleep(1400000);
                ui_menu(selected);
            } else if (selected == 2) {
                retro_loop(pc_ip, pc_port);
                ui_menu(selected);
                prev = ORBIS_PAD_BUTTON_CROSS;
            } else if (selected == 3) {
                return 1;
            } else {
                return 0;
            }
        }

        if (++menuTicks % 60 == 0) menu_heartbeat(pc_ip, pc_port);
        if (changed) ui_menu(selected);
        sceKernelUsleep(30000);
    }
}

static int net_http_init(void) {
    int ret;
    if ((int32_t)sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_NET) < 0) return HTTP_FAILED;
    if ((int32_t)sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_HTTP) < 0) return HTTP_FAILED;
    if ((int32_t)sceSysmoduleLoadModuleInternal(ORBIS_SYSMODULE_INTERNAL_SSL) < 0) return HTTP_FAILED;

    if (!netCoreStarted) {
        ret = sceNetInit();
        (void)ret;
        netCoreStarted = 1;
    }

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
    libhttpCtxId = 0;
    libsslCtxId = 0;
    libnetMemId = 0;
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

static int http_open_get(const char *url, const char *token, int *tpl_out, int *conn_out, int *req_out, int *status_out) {
    int tpl = 0, conn = 0, req = 0;
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

    sceHttpSetConnectTimeOut(req, 3000000);
    sceHttpSetRecvTimeOut(req, 3000000);

    if (sceHttpSendRequest(req, NULL, 0) < 0) goto fail;

    int32_t status = 0;
    if (sceHttpGetStatusCode(req, &status) < 0) goto fail;
    if (status_out) *status_out = status;

    *tpl_out = tpl;
    *conn_out = conn;
    *req_out = req;
    return 1;

fail:
    if (req > 0) sceHttpDeleteRequest(req);
    if (conn > 0) sceHttpDeleteConnection(conn);
    if (tpl > 0) sceHttpDeleteTemplate(tpl);
    return 0;
}

static void http_close_get(int tpl, int conn, int req) {
    if (req > 0) sceHttpDeleteRequest(req);
    if (conn > 0) sceHttpDeleteConnection(conn);
    if (tpl > 0) sceHttpDeleteTemplate(tpl);
}

static int http_get_text(const char *url, const char *token, char *out, size_t out_cap, int *status_out) {
    int tpl = 0, conn = 0, req = 0;
    size_t used = 0;
    if (!http_open_get(url, token, &tpl, &conn, &req, status_out)) return 0;

    while (used + 1 < out_cap) {
        int n = sceHttpReadData(req, out + used, (uint32_t)(out_cap - used - 1));
        if (n < 0) {
            http_close_get(tpl, conn, req);
            return 0;
        }
        if (n == 0) break;
        used += (size_t)n;
    }
    out[used] = 0;
    http_close_get(tpl, conn, req);
    return 1;
}

static int http_get_file(const char *url, const char *token, const char *path, size_t *bytes_out, int *status_out) {
    int tpl = 0, conn = 0, req = 0;
    if (bytes_out) *bytes_out = 0;
    if (!http_open_get(url, token, &tpl, &conn, &req, status_out)) return 0;
    if (!status_out || *status_out != 200) {
        http_close_get(tpl, conn, req);
        return 0;
    }

    FILE *f = fopen(path, "wb");
    if (!f) {
        http_close_get(tpl, conn, req);
        return 0;
    }

    char buf[8192];
    size_t total = 0;
    for (;;) {
        int n = sceHttpReadData(req, buf, sizeof(buf));
        if (n < 0) {
            fclose(f);
            http_close_get(tpl, conn, req);
            return 0;
        }
        if (n == 0) break;
        if (fwrite(buf, 1, (size_t)n, f) != (size_t)n) {
            fclose(f);
            http_close_get(tpl, conn, req);
            return 0;
        }
        total += (size_t)n;
    }

    fclose(f);
    http_close_get(tpl, conn, req);
    if (bytes_out) *bytes_out = total;
    return 1;
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

static int json_extract_int(const char *json, const char *key, int *out) {
    char needle[64];
    snprintf(needle, sizeof(needle), "\"%s\":", key);
    const char *p = strstr(json, needle);
    if (!p) return 0;
    p += strlen(needle);
    *out = atoi(p);
    return 1;
}

static void menu_heartbeat(const char *pc_ip, int pc_port) {
    char token[256], url[256], response[1024];
    int status = 0;
    if (!read_text_file(TOKEN_FILE, token, sizeof(token))) return;
    snprintf(url, sizeof(url), "http://%s:%d/api/v1/status", pc_ip, pc_port);
    http_get_text(url, token, response, sizeof(response), &status);
}

typedef struct { char id[24]; char title[64]; char system[24]; } RetroItem;
static const char *retroSystems[] = {"", "nes", "snes", "gb", "gbc", "gba", "megadrive", "ps1", "n64", "homebrew"};
static const char *retroLabels[] = {"TODOS", "NES", "SNES", "GAME BOY", "GAME BOY COLOR", "GAME BOY ADVANCE", "MEGA DRIVE", "PLAYSTATION", "NINTENDO 64", "HOMEBREW"};

static int retro_load(const char *base, const char *token, int system, int offset,
                      RetroItem items[6], int *total, char *message, size_t cap) {
    char url[384], response[4096];
    int status = 0, count = 0;
    snprintf(url, sizeof(url), "%s/api/v1/retro/library?offset=%d&system=%s", base, offset, retroSystems[system]);
    if (!http_get_text(url, token, response, sizeof(response), &status) || status != 200) {
        snprintf(message, cap, "PC NO DISPONIBLE - HTTP %d", status);
        *total = 0;
        return 0;
    }
    json_extract_int(response, "total", total);
    const char *cursor = strstr(response, "\"items\":[");
    while (cursor && count < 6 && (cursor = strchr(cursor, '{')) != NULL) {
        const char *end = strchr(cursor, '}');
        if (!end) break;
        size_t len = (size_t)(end - cursor + 1);
        if (len >= 512) break;
        char object[512];
        memcpy(object, cursor, len); object[len] = 0;
        memset(&items[count], 0, sizeof(items[count]));
        if (!json_extract_string(object, "rom_id", items[count].id, sizeof(items[count].id))) break;
        json_extract_string(object, "title", items[count].title, sizeof(items[count].title));
        json_extract_string(object, "system", items[count].system, sizeof(items[count].system));
        count++; cursor = end + 1;
    }
    snprintf(message, cap, count ? "X LANZA EN PC - TRIANGULO DETIENE" : "SIN ROMS - CONFIGURA Y ESCANEA EN PC");
    return count;
}

static void retro_draw(RetroItem items[6], int count, int total, int offset,
                       int selected, int system, const char *message) {
    for (int frame = 0; frame < 2; ++frame) {
        draw_base();
        draw_text(160, 385, "RETRO MANAGER", 5, rgb(236,245,255));
        char label[128];
        snprintf(label, sizeof(label), "%s  %d ROMS  PAGINA %d", retroLabels[system], total, offset / 6 + 1);
        draw_text(160, 440, label, 3, rgb(38,181,255));
        for (int i = 0; i < count; ++i) {
            int y = 492 + i * 38;
            if (i == selected) fill_rect(150, y - 5, 1580, 32, rgb(245,185,52));
            snprintf(label, sizeof(label), "%.16s - %.48s", items[i].system, items[i].title);
            draw_text(165, y, label, 3, i == selected ? rgb(5,16,34) : rgb(236,245,255));
        }
        draw_text(160, 735, message, 3, rgb(141,164,190));
        fill_rect(150, 795, 1610, 100, rgb(11,31,59));
        draw_text(160, 810, "IZQ/DER SISTEMA - ARRIBA/ABAJO JUEGO", 3, rgb(141,164,190));
        draw_text(160, 850, "O VOLVER - CUADRADO RECARGA - VIDEO EN PC", 3, rgb(141,164,190));
        present();
    }
}

static void retro_loop(const char *pc_ip, int pc_port) {
    char token[256], base[128], message[128];
    if (!read_text_file(TOKEN_FILE, token, sizeof(token))) return;
    snprintf(base, sizeof(base), "http://%s:%d", pc_ip, pc_port);
    RetroItem items[6];
    int system = 0, offset = 0, selected = 0, total = 0;
    int count = retro_load(base, token, system, offset, items, &total, message, sizeof(message));
    uint32_t prev = ORBIS_PAD_BUTTON_CROSS;
    unsigned ticks = 0, requestSeq = 0;
    retro_draw(items, count, total, offset, selected, system, message);
    for (;;) {
        OrbisPadData data;
        memset(&data, 0, sizeof(data));
        if (scePadReadState(padHandle, &data) < 0) { sceKernelUsleep(30000); continue; }
        uint32_t now = pad_navigation(&data);
        uint32_t pressed = now & ~prev; prev = now;
        int reload = 0, changed = 0;
        if (pressed & ORBIS_PAD_BUTTON_CIRCLE) return;
        if (pressed & ORBIS_PAD_BUTTON_LEFT) { system = (system + 9) % 10; offset = selected = 0; reload = 1; }
        if (pressed & ORBIS_PAD_BUTTON_RIGHT) { system = (system + 1) % 10; offset = selected = 0; reload = 1; }
        if ((pressed & ORBIS_PAD_BUTTON_DOWN) && count) {
            if (selected + 1 < count) selected++;
            else if (offset + count < total) { offset += 6; selected = 0; reload = 1; }
            changed = 1;
        }
        if ((pressed & ORBIS_PAD_BUTTON_UP) && count) {
            if (selected > 0) selected--;
            else if (offset >= 6) { offset -= 6; selected = 5; reload = 1; }
            changed = 1;
        }
        if ((pressed & ORBIS_PAD_BUTTON_CROSS) && count) {
            char url[512], response[1024] = {0}; int status = 0;
            snprintf(url, sizeof(url), "%s/api/v1/retro/launch?id=%s&request=ps4_%ld_%u_%d", base,
                     items[selected].id, (long)time(NULL), ++requestSeq, frameId);
            if (http_get_text(url, token, response, sizeof(response), &status) && status == 200)
                snprintf(message, sizeof(message), "EMULADOR LANZADO EN PC - SIN STREAMING");
            else if (!json_extract_string(response, "error", message, sizeof(message)))
                snprintf(message, sizeof(message), "ERROR DE LANZAMIENTO - REVISA PC");
            changed = 1;
        }
        if (pressed & ORBIS_PAD_BUTTON_TRIANGLE) {
            char url[256], response[512]; int status = 0;
            snprintf(url, sizeof(url), "%s/api/v1/retro/stop", base);
            http_get_text(url, token, response, sizeof(response), &status);
            snprintf(message, sizeof(message), status == 200 ? "EMULADOR DETENIDO" : "NO SE PUDO DETENER");
            changed = 1;
        }
        if (pressed & ORBIS_PAD_BUTTON_SQUARE) reload = 1;
        if (++ticks % 65 == 0) menu_heartbeat(pc_ip, pc_port);
        if (reload) {
            count = retro_load(base, token, system, offset, items, &total, message, sizeof(message));
            if (selected >= count) selected = count ? count - 1 : 0;
            changed = 1;
        }
        if (changed) retro_draw(items, count, total, offset, selected, system, message);
        sceKernelUsleep(30000);
    }
}

static void ack_command(const char *base, const char *token, int cmdId, const char *result) {
    char url[512];
    char tmp[1024];
    int status = 0;
    snprintf(url, sizeof(url), "%s/api/v1/ack?id=%d&result=%s", base, cmdId, result ? result : "ok");
    http_get_text(url, token, tmp, sizeof(tmp), &status);
}

static int responsive_wait_for_menu(const char *pc_ip, int pc_port, uint32_t usec_total) {
    if (padHandle < 0) {
        sceKernelUsleep(usec_total);
        return 0;
    }

    uint32_t prev = 0;
    const uint32_t slice = 50000;
    uint32_t waited = 0;

    while (waited < usec_total) {
        OrbisPadData data;
        memset(&data, 0, sizeof(data));
        if (scePadReadState(padHandle, &data) >= 0) {
            uint32_t now = pad_navigation(&data);
            uint32_t pressed = now & ~prev;
            prev = now;
            if (pressed & ORBIS_PAD_BUTTON_OPTIONS) {
                int reconnect = menu_loop(pc_ip, pc_port);
                if (reconnect) return 1;
                return 0;
            }
        }
        sceKernelUsleep(slice);
        waited += slice;
    }
    return 0;
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
    int padOk = pad_init();
    trace_marker(padOk ? "PAD_04 OK" : "PAD_04 FAIL");

    ui_screen("BUSCANDO PC", "INICIA COMPANION SERVER EN WINDOWS", 1);

    char pc_ip[64] = {0};
    int pc_port = HTTP_PORT;
    if (read_text_file(PC_IP_FILE, pc_ip, sizeof(pc_ip))) {
        trace_marker("DISCOVERY_05 MANUAL IP");
    } else if (!discover_pc(pc_ip, sizeof(pc_ip), &pc_port)) {
        trace_marker("DISCOVERY_05 FAIL");
        ui_screen("PC NO ENCONTRADO", "REVISA FIREWALL O CREA PC_IP.TXT", 0);
        net_http_end();
        for (;;) {}
    } else {
        trace_marker("DISCOVERY_05 OK");
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
        trace_marker("PING_06 FAIL");
        snprintf(detail, sizeof(detail), "HTTP %d", status);
        ui_screen("FALLO PING", detail, 0);
        net_http_end();
        for (;;) {}
    }

    trace_marker("PING_06 OK");
    write_text_file(RESULT_FILE, response);

    char token[256] = {0};
    if (!read_text_file(TOKEN_FILE, token, sizeof(token))) {
        trace_marker("PAIR_07 NEED CODE");
        char pair_code[32] = {0};

        if (padOk) {
            char code6[7];
            if (!input_pair_code(code6)) {
                ui_screen("FALLO MANDO", "NO SE PUDO LEER EL CODIGO", 0);
                net_http_end();
                for (;;) {}
            }
            strncpy(pair_code, code6, sizeof(pair_code) - 1);
        } else if (!read_text_file(PAIR_FILE, pair_code, sizeof(pair_code))) {
            ui_screen("SIN EMPAREJAR", "Mando no disponible. Usa pair_code.txt", 0);
            net_http_end();
            for (;;) {}
        }

        ui_screen("EMPAREJANDO", "VALIDANDO CODIGO CON EL PC", 1);
        snprintf(url, sizeof(url), "%s/api/v1/pair?code=%s&device=PS4", base, pair_code);
        if (!http_get_text(url, NULL, response, sizeof(response), &status) || status != 200 ||
            !json_extract_string(response, "token", token, sizeof(token))) {
            trace_marker("PAIR_07 FAIL");
            snprintf(detail, sizeof(detail), "HTTP %d  REVISA EL CODIGO", status);
            ui_screen("FALLO EMPAREJADO", detail, 0);
            net_http_end();
            for (;;) {}
        }

        write_text_file(TOKEN_FILE, token);
        trace_marker("PAIR_07 OK TOKEN SAVED");
        pad_feedback();
    } else {
        trace_marker("PAIR_07 TOKEN FOUND");
    }

    snprintf(detail, sizeof(detail), "PC %s:%d  TOKEN OK", pc_ip, pc_port);
    ui_screen("CONECTADO", detail, 1);
    trace_marker("HEARTBEAT_08 START");

    int failures = 0;
    int lastCommandId = 0;

    for (;;) {
        snprintf(url, sizeof(url), "%s/api/v1/poll", base);
        status = 0;
        if (http_get_text(url, token, response, sizeof(response), &status) && status == 200) {
            failures = 0;
            write_text_file(RESULT_FILE, response);

            char cmdName[64] = {0};
            int cmdId = 0;
            if (json_extract_string(response, "name", cmdName, sizeof(cmdName)) &&
                json_extract_int(response, "id", &cmdId) &&
                cmdId > 0 && cmdId != lastCommandId) {

                lastCommandId = cmdId;

                if (strcmp(cmdName, "ping") == 0) {
                    trace_marker("COMMAND_09 PING");
                    ui_screen("COMANDO RECIBIDO", "PING DESDE EL PC", 1);
                    pad_feedback();
                    ack_command(base, token, cmdId, "ok");
                } else if (strcmp(cmdName, "message") == 0) {
                    char msg[128] = {0};
                    json_extract_string(response, "text", msg, sizeof(msg));
                    trace_marker("COMMAND_09 MESSAGE");
                    ui_screen("MENSAJE DEL PC", msg[0] ? msg : "SIN TEXTO", 1);
                    pad_feedback();
                    ack_command(base, token, cmdId, "shown");
                    sceKernelUsleep(1700000);
                } else if (strcmp(cmdName, "get_info") == 0) {
                    char fw[64];
                    char result[160];
                    get_firmware(fw, sizeof(fw));
                    snprintf(detail, sizeof(detail), "FIRMWARE %s   APP %s", fw, APP_VERSION);
                    ui_screen("INFO CONSOLA", detail, 1);
                    snprintf(result, sizeof(result), "fw_%s_app_%s", fw, APP_VERSION);
                    trace_marker("COMMAND_09 INFO");
                    ack_command(base, token, cmdId, result);
                    sceKernelUsleep(1300000);
                } else if (strcmp(cmdName, "fetch_file") == 0) {
                    int fileId = 0;
                    char fileName[80] = {0};
                    size_t bytes = 0;
                    json_extract_int(response, "file_id", &fileId);
                    json_extract_string(response, "file_name", fileName, sizeof(fileName));
                    snprintf(detail, sizeof(detail), "%s  DESCARGANDO", fileName[0] ? fileName : "ARCHIVO");
                    ui_screen("RECIBIENDO ARCHIVO", detail, 1);

                    snprintf(url, sizeof(url), "%s/api/v1/file?id=%d", base, fileId);
                    int fileStatus = 0;
                    char result[96];
                    if (fileId > 0 && http_get_file(url, token, RECEIVED_FILE, &bytes, &fileStatus)) {
                        snprintf(detail, sizeof(detail), "%s  %u BYTES", RECEIVED_FILE, (unsigned)bytes);
                        ui_screen("ARCHIVO RECIBIDO", detail, 1);
                        snprintf(result, sizeof(result), "saved_%u_bytes", (unsigned)bytes);
                        trace_marker("COMMAND_09 FILE OK");
                        ack_command(base, token, cmdId, result);
                        pad_feedback();
                    } else {
                        snprintf(result, sizeof(result), "file_error_%d", fileStatus);
                        trace_marker("COMMAND_09 FILE FAIL");
                        ack_command(base, token, cmdId, result);
                        ui_screen("ERROR ARCHIVO", result, 0);
                    }
                    sceKernelUsleep(1500000);
                } else if (strcmp(cmdName, "reconnect") == 0) {
                    trace_marker("COMMAND_09 RECONNECT");
                    ack_command(base, token, cmdId, "reconnecting");
                    ui_screen("RECONECTANDO HTTP", "REINICIANDO ENLACE LOCAL", 1);
                    net_http_end();
                    sceKernelUsleep(500000);
                    if (!net_http_init()) {
                        ui_screen("ERROR DE RED", "NO SE PUDO REINICIAR HTTP", 0);
                        for (;;) {}
                    }
                    sceKernelUsleep(400000);
                } else {
                    trace_marker("COMMAND_09 UNKNOWN");
                    ui_screen("COMANDO RECIBIDO", cmdName, 1);
                    ack_command(base, token, cmdId, "unknown");
                }
            }

            snprintf(detail, sizeof(detail), "PC %s:%d  HEARTBEAT OK", pc_ip, pc_port);
            ui_screen("CONECTADO", detail, 1);
        } else {
            failures++;
            if (status == 401) {
                trace_marker("HEARTBEAT_08 UNAUTHORIZED");
                ui_screen("TOKEN NO VALIDO", "BORRA TOKEN Y EMPAREJA DE NUEVO", 0);
                for (;;) {}
            }
            if (failures >= 2) {
                trace_marker("HEARTBEAT_08 LOST");
                ui_screen("PC DESCONECTADO", "REINTENTANDO HTTP 8787", 0);
            }
        }

        if (responsive_wait_for_menu(pc_ip, pc_port, 2000000)) {
            trace_marker("MENU_RECONNECT");
            ui_screen("RECONECTANDO HTTP", "SOLICITADO DESDE EL MANDO", 1);
            net_http_end();
            sceKernelUsleep(400000);
            if (!net_http_init()) {
                ui_screen("ERROR DE RED", "NO SE PUDO REINICIAR HTTP", 0);
                for (;;) {}
            }
        }
    }
}

