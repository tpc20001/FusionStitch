"""FusionStitch phone app (PWA). Same shell, components and habits as TikLive's phone page:
sticky header + tab row, floating 📊 效能 / 🖥 背景 / ✂ 轉檔 pills, 工具 hub, 設定 with category
column and 更新包 sub-tab, 📋 LOG 與問題, 🆕 版本與更新. CSS/JS patterns are ported from TikLive."""

PAGE = r"""<!doctype html><html lang="zh-Hant" data-mode="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0a0e13"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><title>FusionStitch</title>
<link rel="icon" href="/icon-192.png"><link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="application-name" content="FusionStitch"><meta name="apple-mobile-web-app-title" content="FusionStitch"><style>
:root{--bg:#0e1116;--nav:#0a0e13;--panel:#11161c;--card:#151a21;--line:#252c35;--line2:#2a3340;--text:#e8ecf2;--strong:#fff;--sub:#8994a4;--muted:#7f8a99;--hover:#161e28;
--tab:#11161c;--tabon:#202a35;--tabline:#405064;--head:#191f27;--headtext:#aeb7c4;--chart:#0d1218;--grid:#26303b;--vline:#43d9ff;--track:#2a3038;--btn:#171d26;--btnh:#222b37;
--green:#45d483;--red:#ff4d6d;--purple:#9b6dff;--orange:#f0ad4e;--pink:#ff5c8a;--blue:#4dc9ff;--accent:#e51e63;--safe:env(safe-area-inset-bottom,0px)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 system-ui,-apple-system,"Segoe UI","Microsoft JhengHei","PingFang TC",sans-serif}
button{font:inherit;cursor:pointer;color:inherit}input,select{font:inherit;color:inherit}
header{position:sticky;top:0;z-index:20;background:var(--nav);border-bottom:1px solid var(--line);padding-top:env(safe-area-inset-top,0px)}
.bar{display:flex;align-items:center;justify-content:space-between;padding:8px 12px 6px}
.logo{font-weight:800;font-size:15px;color:var(--strong);display:flex;align-items:center;gap:7px;letter-spacing:.2px;white-space:nowrap;flex:none}
.logo img{width:22px;height:22px;border-radius:6px;display:block}
.clk{font-size:11px;color:var(--sub);font-variant-numeric:tabular-nums}.rt{display:flex;align-items:center;justify-content:flex-end;gap:8px;min-width:0;flex:1;margin-left:8px}.rt .clk{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.ver{font-size:11px;color:var(--sub);border:1px solid var(--line);border-radius:8px;padding:1px 6px;white-space:nowrap;font-variant-numeric:tabular-nums}
.pc{margin-left:6px;font-size:11px;font-weight:800;border-radius:6px;padding:1px 7px;background:color-mix(in srgb,var(--green) 22%,transparent);color:var(--green)}
.inst{background:var(--accent);color:#fff;border:0;border-radius:14px;padding:4px 11px;font-weight:800;font-size:12.5px}
nav{display:flex;gap:6px;padding:0 10px 8px}
nav button{flex:2;background:var(--tab);border:1px solid var(--line);color:var(--sub);border-radius:9px;padding:8px 0;font-weight:700;white-space:nowrap}
nav button.on{background:var(--tabon);border-color:var(--tabline);color:var(--strong)}
nav button.tset{flex:1;font-size:13px}
nav em{font-style:normal;margin-left:5px;background:var(--green);color:#06210f;border-radius:9px;padding:0 6px;font-size:11px}
main{padding:10px 10px calc(30px + var(--safe))}
.empty{color:var(--sub);text-align:center;padding:34px 0}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;margin-bottom:10px}
.sh{display:flex;align-items:center;gap:8px;font-weight:800;font-size:13px;margin:14px 2px 8px;padding-bottom:5px;border-bottom:1px solid;color:var(--sub);border-color:var(--line)}
.sh.live{color:var(--green);border-color:color-mix(in srgb,var(--green) 35%,transparent)}
.search{width:100%;background:var(--card);border:1px solid var(--line);border-radius:9px;color:var(--text);padding:9px 11px;font:inherit;font-size:16px;outline:none}
.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.tools .search{flex:1;min-width:0}
.seg{display:flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;flex:none}
.seg button{background:var(--card);border:0;border-left:1px solid var(--line);color:var(--sub);height:40px;min-width:34px;padding:0 9px;font-weight:800}
.seg button:first-child{border-left:0}.seg button.on{background:var(--tabon);color:var(--strong)}
.pill{flex:1;min-width:0;background:var(--card);border:1px solid var(--line);color:var(--text);border-radius:9px;height:38px;padding:0 10px;font:inherit;font-weight:700;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
select.pill{appearance:auto}
.pill.on{background:var(--tabon);border-color:var(--tabline)}
.chip{font-size:11px;border-radius:6px;padding:1px 6px;font-weight:800;white-space:nowrap;flex:none}
.chip.live{background:color-mix(in srgb,var(--green) 22%,transparent);color:var(--green)}
.chip.run{background:color-mix(in srgb,var(--blue) 20%,transparent);color:var(--blue)}
.chip.warn{background:color-mix(in srgb,var(--orange) 22%,transparent);color:var(--orange)}
.chip.bad{background:color-mix(in srgb,var(--red) 22%,transparent);color:var(--red)}
.chip.sz{background:color-mix(in srgb,var(--vline) 10%,transparent);color:var(--vline);border:1px solid color-mix(in srgb,var(--vline) 35%,transparent);font-variant-numeric:tabular-nums}
.chip.t{color:var(--muted);border:1px solid var(--line)}
.btn{display:block;width:100%;margin-top:10px;padding:13px;border:0;border-radius:10px;background:var(--accent);color:#fff;font-size:16px;font-weight:800;text-align:center;text-decoration:none}
.btn.s{background:var(--btn);border:1px solid var(--line);color:var(--text);font-size:15px}
.btn:disabled{opacity:.45}
.back{background:none;border:0;color:var(--vline);font-size:15px;padding:2px 0 8px}
.toast{position:fixed;left:50%;transform:translateX(-50%);border:1px solid var(--line2);padding:8px 14px;border-radius:10px;display:none;text-align:center;
 top:auto;bottom:20vh;pointer-events:none;background:rgba(20,24,32,.9);color:#fff;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);max-width:min(90vw,560px);font-size:13px;line-height:1.45;box-shadow:0 6px 22px rgba(0,0,0,.45);z-index:10000}
/* clip / job / output cards */
.c{background:var(--card);border:1px solid var(--line2);border-radius:9px;padding:7px 9px 7px;margin-bottom:8px}
.c.live{border:2px solid var(--green)}.c.run{border:2px solid var(--blue)}.c.bad{border:2px solid var(--red)}.c.wait{border:2px solid var(--orange)}
.cb{display:flex;gap:9px;align-items:center}
.th{width:96px;height:54px;border-radius:7px;flex:none;object-fit:cover;background:#0b0e13;border:1px solid var(--line2)}
.ci{flex:1;min-width:0}.r1{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.nm{font-weight:800;color:var(--strong);min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:15px}
.r2{display:flex;gap:4px 10px;font-size:12.5px;color:var(--sub);margin-top:3px;align-items:center;flex-wrap:wrap;font-variant-numeric:tabular-nums}
.acts{display:flex;gap:6px;margin-top:7px;flex-wrap:wrap}.acts .pill{flex:1 1 auto;text-align:center;height:34px}
.ck{width:22px;height:22px;flex:none;accent-color:var(--accent)}
.pb{display:block;height:5px;border-radius:3px;background:var(--track);margin-top:6px;overflow:hidden}.pb u{display:block;height:100%;background:var(--accent)}
.c.run .pb u{background:var(--blue)}
.selbar{position:sticky;bottom:calc(8px + var(--safe));display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:var(--card);border:1px solid var(--accent);border-radius:12px;padding:10px;box-shadow:0 6px 24px rgba(0,0,0,.35);margin-top:8px}
.selbar[hidden]{display:none}
/* filter / action bars stay under the header while the list scrolls */
.stk{position:sticky;top:var(--hh,96px);z-index:15;background:var(--bg);margin:-10px -10px 8px;padding:8px 10px 8px;border-bottom:1px solid var(--line);box-shadow:0 6px 12px -8px rgba(0,0,0,.6)}
.stk .seg{overflow-x:auto;scrollbar-width:none;max-width:100%}.stk .seg::-webkit-scrollbar{display:none}.stk .seg button{flex:none;white-space:nowrap;height:36px}
.stk .tools{flex-wrap:nowrap}.stk .tools+.tools,.stk .tools+.g2,.stk .g2+.tools{margin-top:6px}.stk .snote{margin:4px 2px 0}
.stk #player video{max-height:32vh}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:6px}.g2 .pill{text-align:center}
@media(max-width:700px){header{padding-top:calc(env(safe-area-inset-top,0px) + 34px)}}   /* room for the 📊🖥✂ pills above the title */
.selbar span{flex:1 1 100%;font-weight:700;color:var(--strong)}.selbar .btn{flex:1 1 90px;width:auto;margin:0;padding:11px;font-size:14px}
video{width:100%;border-radius:8px;background:#000;margin-top:6px}
/* settings (TikLive) */
.stabs{display:flex;gap:6px;margin-bottom:10px}
.stabs button{flex:1;min-width:0;background:var(--tab);border:1px solid var(--line);color:var(--sub);border-radius:9px;padding:8px 2px;font-weight:700;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stabs button[data-p=set]{flex:3}.stabs button.on{background:var(--tabon);border-color:var(--tabline);color:var(--strong)}
.s2{display:flex;gap:10px;align-items:flex-start}
.scat{position:sticky;top:calc(var(--hh,96px) + 8px);flex:none;width:clamp(92px,26vw,200px);display:flex;flex-direction:column;gap:5px;max-height:calc(100vh - var(--hh,96px) - 20px);overflow-y:auto}
.scat .sch{font-size:11px;color:var(--sub);margin:6px 4px 0;font-weight:700}
.scat button{text-align:left;background:var(--tab);border:1px solid var(--line);color:var(--sub);border-radius:9px;padding:9px 8px;font-weight:700;font-size:13px;line-height:1.25}
.scat button.on{background:var(--tabon);border-color:var(--tabline);color:var(--strong)}
.scat button span{display:block}.scat button small{display:block;font-size:10.5px;font-weight:400;opacity:.7;margin-top:2px;line-height:1.3}
.ssearch{width:100%;height:34px;border-radius:9px;border:1px solid var(--line);background:var(--card);color:inherit;padding:0 8px;font-size:13px;margin-bottom:2px}
.sbody{flex:1;min-width:0}.stt{font-size:19px;margin:2px 2px 12px;color:var(--strong)}
.ssec{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:6px 14px;margin-bottom:14px}
.ssec h3{font-size:13px;color:var(--accent);margin:10px 0 2px;letter-spacing:.5px}
.si{padding:14px 0;border-bottom:1px solid var(--line)}.si:last-child{border-bottom:0}
.si>b{display:block;color:var(--strong);margin-bottom:9px;font-size:15px}
.si small{display:block;color:var(--sub);font-size:12px;margin-top:8px;line-height:1.5}.si .seg{flex-wrap:wrap}
.si.chg{background:linear-gradient(90deg,color-mix(in srgb,var(--accent) 14%,transparent),transparent 70%);margin:0 -14px;padding-left:14px;padding-right:14px}
.pnum{display:flex;align-items:center;gap:6px}
.pnum button{width:42px;height:40px;border-radius:10px;border:1px solid var(--line);background:var(--btn);color:var(--text);font-size:20px;font-weight:700}
.pnum input{width:96px;height:40px;text-align:center;border-radius:10px;border:1px solid var(--line);background:var(--btn);color:var(--strong);font-size:16px;font-weight:700}
.psel{width:100%;height:42px;border-radius:10px;border:1px solid var(--line);background:var(--btn);color:var(--strong);font-size:15px;padding:0 10px}
.ptxt{color:var(--sub);word-break:break-all}
.pinp{width:100%;height:42px;border-radius:10px;border:1px solid var(--line);background:var(--btn);color:var(--strong);font-size:16px;padding:0 10px}
.snote{font-size:12px;color:var(--sub);line-height:1.5;margin:8px 2px}
.sflash{outline:2px solid #3fb6f2;outline-offset:3px;border-radius:8px;transition:outline-color .3s}
.sres{display:flex;flex-direction:column;align-items:flex-start;gap:2px;width:100%;text-align:left;padding:10px;margin:0 0 6px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:inherit;cursor:pointer}
.sres small{font-size:11px;color:var(--sub)}.sres b{font-size:14px}
.swc{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.sw{width:34px;height:34px;border-radius:50%;border:2px solid transparent}.sw.on{border-color:var(--strong)}
@media(max-width:420px){.scat{width:88px}.scat button{font-size:12px;padding:8px 6px}.ssec{padding:4px 11px}.si.chg{margin:0 -11px;padding-left:11px;padding-right:11px}}
@media (max-width:520px){.scat button small{display:none}}
/* tools hub + sub page header */
.tlh{display:flex;align-items:center;gap:10px;margin:0 0 8px}.tlh b{font-size:16px}.tlh .back{width:36px;height:36px;border-radius:10px;border:1px solid var(--line);background:var(--tab);color:inherit;font-size:20px;padding:0}
.tgh{font-weight:800;font-size:14px;margin:14px 2px 6px;color:var(--sub)}
.tbgS{padding:12px;border-radius:12px;border:1px solid rgba(63,182,242,.4);background:rgba(63,182,242,.1);font-size:13.5px;cursor:pointer}.tbgS span{float:right;opacity:.75}
.wsGrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.wsCard{display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:inherit;text-align:left;cursor:pointer}
.wsCard i{font-style:normal;font-size:22px}.wsCard b{font-size:14px}.wsCard small{font-size:11.5px;opacity:.7;line-height:1.35}
.tool1{padding:10px}
/* floating pills + popup (TikLive V0.7.16–0.7.36) */
#sysFab{position:fixed;top:calc(env(safe-area-inset-top,0px) + 7px);left:50%;transform:translateX(-50%);z-index:9810;display:flex;gap:6px;pointer-events:none}
#sysFab button{pointer-events:auto;height:24px;padding:0 8px;border-radius:12px;border:1px solid rgba(140,150,165,.5);background:rgba(18,22,30,.88);color:#fff;font-weight:800;font-size:11px;margin:0;cursor:pointer;white-space:nowrap;box-shadow:0 2px 8px rgba(0,0,0,.35);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);position:relative;overflow:visible}
#sysFab button.on{background:#1f8ef1;border-color:#1f8ef1}
#sysFab .fbN,#sysFab .fbR{position:absolute;min-width:16px;height:16px;padding:0 4px;border-radius:8px;font:800 10px/16px system-ui,sans-serif;font-style:normal;text-align:center;box-sizing:border-box;pointer-events:none;box-shadow:0 1px 4px rgba(0,0,0,.5)}
#sysFab .fbN{top:-7px;right:-6px;background:#ff3b5c;color:#fff}#sysFab .fbR{bottom:-7px;right:-6px;background:#1f8ef1;color:#fff}
#sysFab .fbN[hidden],#sysFab .fbR[hidden]{display:none!important}
#sysPop{position:fixed;inset:0;z-index:9800;background:rgba(0,0,0,.28)}
.sysPopW{position:absolute;left:15%;width:70%;top:calc(env(safe-area-inset-top,0px) + 38px);height:50%;display:flex;flex-direction:column;background:var(--bg,#0d1015);color:var(--text,#e8ecf2);border:1px solid rgba(140,150,165,.45);border-radius:12px;box-shadow:0 14px 40px rgba(0,0,0,.6);overflow:hidden}
.sysPopW>*{zoom:.78}
@media (min-width:900px){.sysPopW{left:calc(50% - 360px);width:720px}}
@media (max-width:520px){.sysPopW{left:4%;width:92%}}
.sysPopH{display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid rgba(127,127,127,.25)}.sysPopH b{font-size:15px;white-space:nowrap}.sysPopN{flex:1;font-size:11.5px;opacity:.6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sysPopB[hidden]{display:none!important}.sysPopG{padding-top:8px}.sysPopG .bgsHd>b{display:none}
.sysPopX{width:40px;height:32px;margin:0;border:0;border-radius:9px;background:rgba(127,127,127,.18);color:inherit;font-size:16px;cursor:pointer}
.sysPopB{flex:1 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:0 10px 12px;-webkit-overflow-scrolling:touch}.sysPopB .stt{display:none}
.sysPopR{flex:none;height:22px;display:flex;align-items:center;justify-content:center;cursor:ns-resize;touch-action:none;border-top:1px solid rgba(127,127,127,.25);background:rgba(127,127,127,.06)}
.sysPopR i{width:56px;height:5px;border-radius:3px;background:rgba(160,170,185,.6)}.sysPopR.on{background:rgba(31,142,241,.18)}.sysPopR.on i{background:#1f8ef1}
@media (pointer:coarse){.sysPopR{height:34px}.sysPopR i{width:70px;height:6px}}
.edjH{font-size:12px;opacity:.7;margin:10px 0 4px;font-weight:700}
.edjI{border:1px solid rgba(140,150,165,.3);border-radius:10px;padding:7px 9px;margin:0 0 6px;cursor:pointer;background:rgba(127,127,127,.06)}
.edjI.run{border-color:#1f8ef1}.edjI.wait{opacity:.85}
.edjT{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.edjT b{font-size:13.5px}.edjT em{font-style:normal;font-size:11px;font-weight:800;padding:1px 7px;border-radius:9px;background:#f0ad4e;color:#111}.edjT span{font-size:11.5px;opacity:.7}
.edjI.run .edjT em{background:#1f8ef1;color:#fff}.edjI.done .edjT em{background:#45d483;color:#111}.edjI.err .edjT em{background:#ff4d6d;color:#fff}.edjGo{margin-left:auto;color:#4dc9ff;opacity:1!important;font-weight:800}
.edjI small{display:block;font-size:11.5px;opacity:.75;margin-top:2px}
.edjP{display:block;height:4px;border-radius:2px;background:rgba(127,127,127,.25);margin-top:4px;overflow:hidden}.edjP u{display:block;height:100%;background:#1f8ef1}
/* 🖥 後台狀態 */
.bgs{display:flex;flex-direction:column;gap:8px;font-size:13.5px}
.bgsHd{display:flex;align-items:baseline;gap:10px}.bgsHd b{font-size:16px}.bgsSum{font-size:13px;opacity:.85}.bgsNote{font-size:12px;opacity:.7}
#bgsL{display:flex;flex-direction:column;gap:8px}
.bgsC{border:1px solid var(--line);border-radius:12px;padding:8px 10px;background:var(--card)}.bgsC.idle{opacity:.55;padding:6px 10px}
.bgsCH{display:flex;align-items:center;gap:8px}.bgsCH b{flex:1}.bgsCH span{font-size:12px;opacity:.8}.bgsCN{font-size:11.5px;opacity:.65;margin:2px 0 4px}
.bgsI{display:flex;align-items:center;gap:8px;padding:6px 0;border-top:1px solid var(--line)}
.bgsI>i{font-style:normal;flex:none;width:18px;text-align:center}.bgsI.run>i{color:#3ccf8e}.bgsI.wait>i{opacity:.7}
.bgsT{flex:1;min-width:0}.bgsT b{display:block;font-size:13px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bgsT small{font-size:11.5px;opacity:.7}
.bgsT u{display:block;height:4px;border-radius:2px;background:rgba(127,127,127,.25);margin-top:4px;overflow:hidden}.bgsT u s{display:block;height:100%;background:#3fb6f2;text-decoration:none}
.bgsI button{flex:none;height:28px;padding:0 10px;border-radius:999px;border:1px solid rgba(255,101,119,.55);background:transparent;color:#ff6577;font-weight:700;cursor:pointer}
.bgsLst{max-height:252px;overflow-y:auto;overscroll-behavior:contain}.bgsMore{font-size:11px;opacity:.6;text-align:center;padding-top:3px}.bgsGuard{color:#f0ad4e;font-weight:700}
/* 📊 效能 */
.pfbig canvas.pfcv{width:100%;height:170px;display:block;margin-top:6px}
.pfhd{display:flex;justify-content:space-between;align-items:baseline;padding-top:10px}.pfhd b{font-size:18px}.pfhd span{font-size:22px;font-weight:800;color:var(--strong)}
.pfax{display:flex;justify-content:space-between;font-size:11px;color:var(--sub);margin:3px 0 6px}
.pfdt{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px 14px;padding:6px 0 12px}
.pfdt div{display:flex;flex-direction:column}.pfdt span{font-size:12px;color:var(--sub)}.pfdt b{font-size:15px;color:var(--strong);word-break:break-all}
.pft{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-bottom:14px}
.pftl{display:flex;gap:8px;align-items:center;text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px;color:var(--text)}
.pftl.on{border-color:var(--accent);background:var(--tabon)}.pftl canvas{width:56px;height:40px;flex:none}
.pftl div{min-width:0;display:flex;flex-direction:column}.pftl b{font-size:13px;color:var(--strong)}.pftl span{font-size:15px;font-weight:800}
.pftl small{font-size:11px;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pftemp{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:8px;padding:8px 0 12px}
.pftemp div{background:var(--btn);border:1px solid var(--line);border-radius:10px;padding:8px 10px;display:flex;flex-direction:column}
.pftemp span{font-size:12px;color:var(--sub)}.pftemp b{font-size:20px;color:var(--strong)}.pftemp b.warm{color:#f0ad4e}.pftemp b.hot{color:#ff4d6d}
.stg{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:8px;margin:6px 0 12px}
.sttl{background:var(--btn);border:1px solid var(--line);border-radius:10px;padding:9px 10px;display:flex;flex-direction:column;gap:3px;min-width:0}
.sttl b{font-size:12.5px;color:var(--strong);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sttl>span{font-size:16px;font-weight:800;color:var(--strong)}
.sttl i{display:block;height:5px;border-radius:3px;background:var(--track);overflow:hidden}.sttl i u{display:block;height:100%;background:var(--accent)}
.sttl.warn i u{background:#f0ad4e}.sttl.bad i u{background:#ff4d6d}.sttl small{font-size:11px;color:var(--sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* 🆕 版本與更新 */
.chv{display:flex;flex-direction:column;gap:8px}.chvH{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}.chvH b{font-size:18px}.chvH span{opacity:.75;font-size:13px}
.chvNw{padding:9px 12px;border-radius:12px;background:rgba(77,201,255,.12);border:1px solid rgba(77,201,255,.45);display:flex;gap:8px;align-items:center;flex-wrap:wrap}.chvNw button{margin-left:auto;width:auto}
.chvOk{opacity:.7;font-size:13px}.chvE{opacity:.7;padding:16px 4px}
.chvBar{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.chvQ{flex:1 1 220px;min-width:0;padding:8px 10px;border-radius:10px;border:1px solid var(--line);background:var(--card)}.chvBar .pill{flex:0 0 auto;width:auto;white-space:nowrap}
.chvL{display:flex;flex-direction:column;gap:6px}.chvV{border-radius:12px;border:1px solid rgba(140,150,165,.22);background:rgba(140,150,165,.07);overflow:hidden}.chvV.nw{border-color:rgba(77,201,255,.6)}
.chvVh{display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:none;border:0;padding:9px 12px;color:inherit;cursor:pointer;flex-wrap:wrap}
.chvVh i{font-style:normal;opacity:.7;width:12px}.chvVh em{font-style:normal}.chvVh u{text-decoration:none;font-size:11px;padding:1px 7px;border-radius:9px;background:rgba(69,212,131,.18)}.chvVh span{margin-left:auto;opacity:.65;font-size:12px}
.chvI{padding:0 12px 10px 32px;display:flex;flex-direction:column;gap:4px;font-size:13.5px;line-height:1.5}.chvI mark{background:rgba(255,200,60,.45);color:inherit;border-radius:3px;padding:0 1px}
.chvD{opacity:.7;font-size:12.5px}.chvF{opacity:.6}
/* 更新包 */
.abt{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;font-size:14px}.abt span{color:var(--sub)}.abt b{color:var(--strong)}
.ufile{position:relative;overflow:hidden}.ufile input{position:absolute;inset:0;opacity:0;cursor:pointer;font-size:0}
.ucard{border:1px solid var(--accent);border-radius:12px;padding:12px;margin-top:10px}.ucard .uvs{font-size:17px;font-weight:800;color:var(--strong)}.ucard ul{margin:8px 0 0;padding-left:18px;font-size:13.5px;line-height:1.55}
.utag{font-size:12px;padding:1px 8px;border-radius:99px;margin-left:6px;background:var(--accent);color:#fff;vertical-align:2px}.utag.old{background:#c62845}.utag.same{background:var(--btn);color:var(--text);border:1px solid var(--line)}
.uok{color:#45d483;font-weight:700;margin-top:8px}.ubad{color:#ff6577;margin-top:8px}.uwarn{color:#f0ad4e;font-size:13px;margin-top:8px}
.ubtns{display:flex;gap:8px}.ubtns .btn{flex:1}.umsg{margin-top:8px;min-height:18px;font-size:14px}
.ubar{height:6px;border-radius:3px;background:var(--btn);overflow:hidden;margin-top:8px}.ubar i{display:block;height:100%;background:var(--accent);width:0}
.upk{border:1px solid var(--line);border-radius:12px;padding:10px;margin-bottom:8px}.uh{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}.ut{font-size:11px;padding:0 6px;border-radius:6px;border:1px solid var(--line);color:var(--sub)}.uh small{color:var(--sub)}
/* LOG */
.log{font-family:Consolas,monospace;font-size:12px;background:#080b0f;color:#b8f7cf;border:1px solid #26313b;border-radius:6px;padding:6px;white-space:pre-wrap;word-break:break-all;overflow:auto;max-height:60vh}
.strow{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}.strow input{background:var(--btn);border:1px solid var(--line);border-radius:8px;padding:6px;color:var(--text)}
/* folder picker (bottom sheet) */
.calov{position:fixed;inset:0;z-index:60;background:rgba(0,0,0,.55);display:flex;align-items:flex-end;justify-content:center}
@media(min-width:700px){.calov{align-items:center}}
.cal{background:var(--panel);border:1px solid var(--line2);border-radius:16px 16px 0 0;width:min(560px,100vw);max-height:86vh;max-height:86dvh;display:flex;flex-direction:column;padding-bottom:var(--safe)}
@media(min-width:700px){.cal{border-radius:16px}}
.calhd{display:flex;justify-content:space-between;align-items:center;padding:12px 14px 6px}.calhd b{color:var(--strong);font-size:16px}.calx{background:none;border:0;color:var(--sub);font-size:18px}
.calbody{overflow-y:auto;padding:0 10px 6px;flex:1;min-height:0}.dirit{padding:11px 6px;border-bottom:1px solid var(--line);cursor:pointer}
.calft{display:flex;gap:8px;align-items:center;padding:10px 12px;border-top:1px solid var(--line)}.calsel{flex:1;color:var(--text);font-weight:700;font-size:13px;word-break:break-all}
.calclr{background:var(--btn);border:1px solid var(--line);color:var(--text);border-radius:10px;padding:9px 14px}.calok{background:var(--accent);border:0;color:#fff;border-radius:10px;padding:9px 18px;font-weight:800}
.lvb{--lc:#2ecc71;display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 9px 0 8px;border-radius:13px;border:1px solid color-mix(in srgb,var(--lc) 55%,transparent);background:color-mix(in srgb,var(--lc) 12%,transparent);color:var(--lc);font:700 12px/1 system-ui;cursor:pointer;margin-right:8px}
.lvb .lvbars{display:inline-flex;align-items:flex-end;gap:1.5px;height:16px}.lvb .lvbars i{width:3px;border-radius:1px;background:rgba(127,140,160,.35)}.lvb .lvbars i.on{background:var(--lc)}.lvb.off{--lc:#7f8a99}
.lvm{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:12px}
.lvm>div{background:#151a21;color:#e8edf3;border:1px solid #252c35;border-radius:16px;max-width:560px;width:100%;max-height:90vh;overflow:auto;padding:14px 16px;box-sizing:border-box}
.lvm h3{margin:0 0 4px;font-size:16px}.lvm .lvbig{font-size:28px;font-weight:800}.lvm table{width:100%;border-collapse:collapse;font-size:12.5px;margin:8px 0}
.lvm td,.lvm th{padding:5px;border-bottom:1px solid rgba(127,140,160,.18);text-align:left}.lvm .lvmu{opacity:.7;font-size:12px;line-height:1.5}
.lvm .lvrow{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0;font-size:13px}.lvm select{font:inherit;padding:3px 6px;border-radius:8px}.lvm .lvx{float:right;border:0;background:none;color:inherit;font-size:20px;cursor:pointer;opacity:.7}
</style></head><body>
<header><div class="bar"><div class="logo"><img src="/icon-192.png" alt="">FusionStitch<span class="pc" id="pcn">GoPro Fusion 360</span></div><button id="inst" class="inst" hidden>📲 安裝 App</button><div class="rt"><button type="button" class="lvb" id="lvb" title="🤝 效能分配（跟 SortCut、ComfyRemote）：點一下看詳細"><span class="lvbars"><i style="height:5px"></i><i style="height:6px"></i><i style="height:7px"></i><i style="height:8px"></i><i style="height:9px"></i><i style="height:10px"></i><i style="height:11px"></i><i style="height:12px"></i><i style="height:13px"></i><i style="height:14px"></i></span><b>Lv–</b></button><div id="clk" class="clk"></div><span class="ver" id="ver" title="程式版本">V</span></div></div>
<nav id="tabs"><button data-t="quick">★ 快速轉檔</button><button data-t="lib">素材庫</button><button data-t="queue">轉檔佇列<em id="nq" hidden></em></button><button data-t="outs">成品<em id="no" hidden></em></button><button data-t="tools" class="tset">工具</button><button data-t="set" class="tset">設定</button></nav></header>
<main id="v"></main><div class="toast" id="toast"></div>
<script>
"use strict";
const B="";
const $=s=>document.querySelector(s),V=()=>$("#v");
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const p2=n=>String(n).padStart(2,"0");
const lsGet=(k,d)=>{try{const v=localStorage.getItem(k);return v===null?d:v;}catch(e){return d;}};
const lsSet=(k,v)=>{try{localStorage.setItem(k,v);}catch(e){}};
const THEMES={dark:{"--bg":"#0e1116","--nav":"#0a0e13","--panel":"#11161c","--card":"#151a21","--line":"#252c35","--line2":"#2a3340","--text":"#e8ecf2","--strong":"#fff","--sub":"#8994a4","--muted":"#7f8a99","--hover":"#161e28","--tab":"#11161c","--tabon":"#202a35","--tabline":"#405064","--btn":"#171d26","--track":"#2a3038"},
 light:{"--bg":"#f3f5f8","--nav":"#e8ecf1","--panel":"#ffffff","--card":"#ffffff","--line":"#d5dce4","--line2":"#c2cbd6","--text":"#1b2430","--strong":"#0b121a","--sub":"#5d6978","--muted":"#6b7685","--hover":"#dde4ec","--tab":"#e8ecf1","--tabon":"#ffffff","--tabline":"#6f9fd3","--btn":"#ffffff","--track":"#dfe5ec"}};
const UI_DEF={theme:"dark",accent:"",zoom:100,anim:1,showClk:1,showVer:1};
let UI=Object.assign({},UI_DEF);try{Object.assign(UI,JSON.parse(localStorage.getItem("fs_ui")||"{}"));}catch(e){}
function saveUI(){lsSet("fs_ui",JSON.stringify(UI));}
function setHH(){const h=document.querySelector("header");if(h)document.documentElement.style.setProperty("--hh",h.offsetHeight+"px");}
window.addEventListener("resize",setHH);
function uiZoom(){const z=(UI.zoom&&+UI.zoom!==100)?+UI.zoom/100:1,mv=document.querySelector('meta[name=viewport]'),touch=window.matchMedia&&matchMedia("(pointer:coarse)").matches;
  if(touch&&mv){document.body.style.zoom="";const land=window.matchMedia&&matchMedia("(orientation: landscape)").matches,sw=land?Math.max(screen.width,screen.height):Math.min(screen.width,screen.height);
    mv.setAttribute("content",z===1?"width=device-width,initial-scale=1,viewport-fit=cover":`width=${Math.round(sw/z)},initial-scale=${z},viewport-fit=cover`);}
  else{document.body.style.zoom=z===1?"":Math.round(z*100)+"%";}}
function applyUI(){const root=document.documentElement;let mode=UI.theme;
  if(mode==="system")mode=window.matchMedia&&matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";
  for(const [k,v] of Object.entries(THEMES[mode]||THEMES.dark))root.style.setProperty(k,v);root.dataset.mode=mode;
  if(UI.accent)root.style.setProperty("--accent",UI.accent);else root.style.removeProperty("--accent");
  uiZoom();let css="";
  if(!+UI.anim)css+=`*,*::before,*::after{transition:none!important;animation:none!important}`;
  if(!+UI.showClk)css+=`.clk{display:none!important}`;if(!+UI.showVer)css+=`.ver{display:none!important}`;
  let st=document.getElementById("uiDyn");if(!st){st=document.createElement("style");st.id="uiDyn";document.head.appendChild(st);}st.textContent=css;
  setTimeout(setHH,0);const m=document.querySelector('meta[name=theme-color]');if(m)m.content=getComputedStyle(root).getPropertyValue("--nav").trim()||"#0a0e13";}
let S={timer:null,pending:null,sel:new Set()};
async function J(p){const r=await fetch(B+p,{cache:"no-store"});if(r.status===401){location.reload();throw 0;}if(!r.ok)throw 0;return r.json();}
async function post(p,d){const r=await fetch(B+p,{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json"},body:JSON.stringify(d||{})});if(r.status===401){location.reload();throw 0;}let j={};try{j=await r.json();}catch(e){}return j;}
async function act(p,d){let j;try{j=await post(p,d);}catch(e){toast("✖ 連不到電腦");return {};}if(j&&j.message)toast((j.ok===false?"✖ ":"✔ ")+j.message);return j||{};}
function toast(t,ms){const e=$("#toast");e.textContent=t;e.style.display="block";clearTimeout(e._t);e._t=setTimeout(()=>e.style.display="none",ms||2600);}
function later(fn,ms){clearTimeout(S.timer);S.timer=setTimeout(()=>{if(document.hidden){S.pending=fn;return;}fn();},ms);}
document.addEventListener("visibilitychange",()=>{if(document.hidden)return;if(S.pending){const f=S.pending;S.pending=null;f();}});
document.querySelectorAll("#tabs button").forEach(b=>b.onclick=()=>{location.hash=b.dataset.t;});
const offline='<div class="empty">連不到電腦：請確認 FusionStitch 開著、手機的 Tailscale 已連線</div>';
const fmtB=b=>{b=+b||0;return b>=1e12?(b/1e12).toFixed(2)+" TB":b>=1e9?(b/1e9).toFixed(2)+" GB":b>=1e6?(b/1e6).toFixed(b>=1e8?0:1)+" MB":b>=1e3?(b/1e3).toFixed(0)+" KB":b.toFixed(0)+" B";};
const fmtR=b=>fmtB(b)+"/s";
const fd=s=>{if(s==null||s<=0)return"—";s=Math.round(s);const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return(h?h+":"+p2(m):m)+":"+p2(x)};
const fmtClk=ep=>{const d=new Date(ep*1000),t=new Date();const same=d.toDateString()===t.toDateString();return (same?"":(d.getMonth()+1)+"/"+d.getDate()+" ")+p2(d.getHours())+":"+p2(d.getMinutes());};
const ST={queued:["排隊中","wait"],running:["轉檔中","run"],paused:["已暫停","warn"],done:["完成","live"],error:["失敗","bad"],cancelled:["已取消","t"]};
/* back button stack (TikLive V0.6.99) */
const VGB=[];
function vgBackPush(fn){VGB.push(fn);try{history.pushState({vgb:VGB.length},"");}catch(e){}}
function vgBackDone(fn){const i=VGB.lastIndexOf(fn);if(i<0)return;const n=VGB.length-i;VGB.splice(i);try{history.go(-n);}catch(e){}}
function vgBackDrop(fn){const i=VGB.lastIndexOf(fn);if(i>=0)VGB.splice(i,1);}
window.addEventListener("popstate",e=>{const d=(e.state&&e.state.vgb)||0;const F=VGB.splice(Math.min(d,VGB.length));for(let i=F.length-1;i>=0;i--){try{F[i](true);}catch(x){}}});
/* ---------------- folder picker (bottom sheet) ---------------- */
(function(){const st=document.createElement("style");st.textContent=`.drv{padding:10px 8px}.dtop{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.dl{color:var(--strong);font-weight:700}
.dk{font-size:12px;font-weight:900;border-radius:6px;padding:1px 7px;background:#39424f;color:#dfe6ee}.dk.ssd{background:#1d5c3a;color:#bff5d6}.dk.hdd{background:#5a4520;color:#ffe2a8}
.dm{display:block;color:var(--sub);font-size:12px;margin-top:2px}.dbar{display:block;height:7px;background:var(--line);border-radius:4px;overflow:hidden;margin:6px 0 3px}.dbar u{display:block;height:100%}
.dsp{color:var(--sub);font-size:12px}.dsp b{color:var(--strong)}.dhead{padding:8px;margin-bottom:6px;border:1px solid var(--line);border-radius:10px;background:#0f1a24}.dhead .dm{display:inline;margin:0}`;document.head.appendChild(st);})();
function drvTag(v){return v.kind==="SSD"?'<span class="dk ssd">SSD</span>':(v.kind==="HDD"?'<span class="dk hdd">HDD</span>':'<span class="dk">磁碟</span>');}
function drvBar(v){if(!v.total)return "";const u=1-v.free/v.total,c=v.free<50e9||u>.95?"#ff6577":(u>.85?"#ffb347":"#45d483");
  return `<i class="dbar"><u style="width:${(u*100).toFixed(1)}%;background:${c}"></u></i><small class="dsp">剩 <b>${fmtB(v.free)}</b>／共 ${fmtB(v.total)}（已用 ${Math.round(u*100)}%）</small>`;}
function drvCard(v){return `<div class="dirit drv" data-d="${esc(v.drive)}"><div class="dtop"><b>💽 ${esc(v.drive)}</b>${v.label?`<span class="dl">${esc(v.label)}</span>`:""}${drvTag(v)}</div>${v.model?`<small class="dm">${esc(v.model)}</small>`:""}${drvBar(v)}</div>`;}
function drvHead(v){return `<div class="dhead"><div class="dtop"><b>${esc(v.drive)}</b>${v.label?`<span class="dl">${esc(v.label)}</span>`:""}${drvTag(v)}${v.model?`<small class="dm">${esc(v.model)}</small>`:""}</div>${drvBar(v)}</div>`;}
function pickDir(title,start,cb){const ov=document.createElement("div");ov.className="calov";
  ov.innerHTML=`<div class="cal"><div class="calhd"><b>${esc(title)}</b><button class="calx">✕</button></div><div class="calbody" id="dirB"></div><div class="calft"><span class="calsel" id="dirP"></span><button class="calclr" id="dirN">＋ 新增資料夾</button><button class="calclr" id="dirC">取消</button><button class="calok" id="dirOk">選這個資料夾</button></div></div>`;
  document.body.appendChild(ov);let cur="";const close=b=>{ov.remove();if(b!==true)vgBackDone(close);};vgBackPush(close);
  const open=async p=>{let d;try{d=await J("/api/dirs?path="+encodeURIComponent(p));}catch(e){return;}cur=d.path;ov.querySelector("#dirP").textContent=d.path||"選擇磁碟";ov.querySelector("#dirOk").disabled=!d.path;ov.querySelector("#dirN").style.display=d.path?"":"none";
    const dv=d.path?"":(d.drives||[]);
    ov.querySelector("#dirB").innerHTML=(d.path?(d.drive?drvHead(d.drive):"")+`<div class="dirit" data-d="${esc(d.parent||"")}">⬑ ${d.parent?"上一層":"磁碟列表"}</div>`:"")
      +(dv.length?dv.map(drvCard).join(""):d.dirs.map(x=>`<div class="dirit" data-d="${esc(x)}">📁 ${esc(x.split(/[\\/]/).filter(Boolean).pop()||x)}</div>`).join(""))+(d.error?`<div class="ubad">${esc(d.error)}</div>`:"");};
  ov.onclick=e=>{if(e.target===ov||e.target.closest(".calx")||e.target.closest("#dirC")){close();return;}const it=e.target.closest(".dirit");if(it){open(it.dataset.d);return;}
    if(e.target.closest("#dirN")&&cur){const n=(prompt("新資料夾名稱（建立在 "+cur+" 裡面）","")||"").trim();if(!n)return;
      act("/api/mkdir",{parent:cur,name:n}).then(j=>{if(j&&j.ok&&j.path)open(j.path);});return;}if(e.target.closest("#dirOk")&&cur){close();cb(cur);}};
  open(start||"");}
/* ================= 快速轉檔 ================= */
let QSEL=null;
document.addEventListener("click",async e=>{if(!e.target||e.target.id!=="dgGo")return;const o=document.getElementById("dgOut");o.hidden=false;o.textContent="產生中…";
  try{const r=await post("/api/diag",{});o.textContent=(r&&r.text)||"沒有資料";}catch(x){o.textContent="連不到電腦";}});
function qkImg(c){return `<span class="qkTw"><img class="qkTh" loading="lazy" alt="" title="左：前鏡頭　右：後鏡頭（第 5 秒）・點一下放大" src="/api/clippair?id=${encodeURIComponent(c.id)}" onload="this.style.opacity=1;this.parentNode.classList.add('ok')" onerror="qkImgErr(this)" onclick="event.preventDefault();event.stopPropagation();if(this.style.opacity==1)qkBig(this.src)"></span>`;}
function qkImgErr(img){const n=+(img.dataset.n||0)+1;img.dataset.n=n;if(n>300)return;setTimeout(()=>{if(img.isConnected)img.src=img.src.replace(/&r=\d+$/,"")+"&r="+n;},4000);}
function qkCats(ps){const m={};ps.forEach(c=>{(m[c.problem.label]=m[c.problem.label]||[]).push(c);});return Object.entries(m);}
function qkEst(d){if(!QSEL)return "";const cs=d.clips.filter(c=>!c.problem&&QSEL.ids.has(c.id));if(!cs.length)return "";
  let src=0,m=0,lo=0,hi=0;cs.forEach(c=>{src+=c.src||0;m+=c.est||0;lo+=c.est_lo||0;hi+=c.est_hi||0;});
  const need=hi*1.1+2e9,o=d.out_drive;let fit="";
  if(o&&o.total){const ok=o.free>=need,tight=o.free>=hi;
    fit=`<div class="qkTip ${ok?"ok":(tight?"info":"bad")}">${ok?"✓":"⚠"} 成品位置 ${esc(o.drive)}${o.label?" "+esc(o.label):""}${o.kind?"（"+o.kind+"）":""}：剩 ${fmtB(o.free)}／共 ${fmtB(o.total)}，${ok?"夠用":(tight?"剛好夠，建議多留一點":"不夠，請換一顆硬碟或先清出空間")}</div>`;}
  return `<div class="qkEst"><b>這批 ${cs.length} 支</b>：原檔 ${fmtB(src)} → 預估約 <b>${fmtB(m)}</b>${src?`（約 ${Math.round(m/src*100)}%）`:""}<br>安全範圍 ${fmtB(lo)} – ${fmtB(hi)}，建議成品資料夾至少準備 <b>${fmtB(need)}</b><div class="qkTip info">${esc(d.est_basis||"")}</div>${fit}</div>`;}
function qkBig(src){let o=document.getElementById("qkBigO");if(!o){o=document.createElement("div");o.id="qkBigO";
  o.style.cssText="position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.88);display:flex;flex-direction:column;align-items:center;justify-content:center;padding:12px";
  o.innerHTML='<img style="max-width:100%;max-height:85vh;border-radius:10px"><div style="color:#aab4c2;margin-top:10px;font-size:14px">左：前鏡頭　右：後鏡頭（第 5 秒）・點任何地方關閉</div>';
  o.onclick=()=>o.remove();document.body.appendChild(o);}o.querySelector("img").src=src;}
async function viewQuick(){$("#clk").textContent="";const host=V();if(!S.qk)host.innerHTML='<div class="empty">讀取中…</div>';
  let d;try{d=await J("/api/quick");}catch(e){if(!S.qk)host.innerHTML=offline;later(viewQuick,6000);return;}
  if(location.hash&&location.hash!=="#quick")return;
  const sig=JSON.stringify([d.folder,d.dirs,d.recent,d.clips.map(c=>[c.id,c.status,c.duration,c.time,c.problem?c.problem.cat:"",Math.round((c.est||0)/1e8),c.done?1:0]),d.est_basis,d.out_drive?Math.round(d.out_drive.free/1e9):0,d.out,d.out_mode,d.tip,d.relay,d.relay_on,d.preset]);
  const same=S.qk&&S.qkSig===sig&&document.getElementById("qkJobs");S.qk=d;S.qkSig=sig;
  if(QSEL===null||QSEL.folder!==d.folder){QSEL={folder:d.folder,ids:new Set(d.clips.filter(c=>c.status===""&&!c.done&&c.complete&&!c.problem).map(c=>c.id)),touched:false};}
  else if(!QSEL.touched){d.clips.forEach(c=>{if(c.status===""&&!c.done&&c.complete&&!c.problem)QSEL.ids.add(c.id);});}
  d.clips.forEach(c=>{if(c.problem)QSEL.ids.delete(c.id);});
  const good0=d.clips.filter(c=>!c.problem),probs=d.clips.filter(c=>c.problem);const nDone=good0.filter(c=>c.done||c.status==="已轉檔").length;
  let QHIDE=false;try{QHIDE=localStorage.getItem("fsQHide")==="1";}catch(e){}
  const good=QHIDE?good0.filter(c=>!(c.done||c.status==="已轉檔")):good0;
  const nm=x=>esc(x.split(/[\\/]/).filter(Boolean).pop()||x);
  const st=c=>c.status||(c.done?"✓ 已轉過":(c.complete?"未轉檔":"不完整"));
  const sel=good.filter(c=>QSEL.ids.has(c.id)).length;
  const jobs=d.jobs.map(j=>{const fin=j.finish?`預計 ${fmtClk(j.finish)} 完成`:"";const p=(j.progress*100);
    return `<div class="qkJ"><div class="qkJt"><b>${esc(j.title)}</b><em class="${j.status==="running"?"ok":""}">${j.status==="running"?(j.eta_ready?fin+"（剩 "+fd(j.finish-Date.now()/1000)+"）":"準備中…"):(j.status==="queued"?"排隊中 "+fin:"已暫停")}</em></div>
    ${j.status==="running"?`<i class="qkP"><u style="width:${p.toFixed(1)}%"></u></i><small>${p.toFixed(1)}% · ${j.fps.toFixed(1)} 格/秒 · ${esc(j.detail)}</small>`:`<small>${esc(j.name)}</small>`}</div>`;}).join("");
  const fin=d.all_finish?`<div class="qkTip ok" style="margin:0 0 6px">⏱ 預計 ${fmtClk(d.all_finish.at)} 全部完成（${d.all_finish.n} 支）</div>`:"";
  if(same){document.getElementById("qkJobs").innerHTML=fin+(jobs||'<div class="qkTip info">目前沒有轉檔工作</div>');later(viewQuick,3000);return;}
  host.innerHTML=`<style>.qk{padding:10px 12px 90px;max-width:760px;margin:auto}.qkC{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;margin:10px 0}
.qkH{color:#4dc9ff;font-weight:900;margin-bottom:6px}.qkPath{font-weight:800;color:var(--strong);word-break:break-all;margin:4px 0 8px}
.qkRow{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.qkRow button,.qkRow select{background:var(--btn);border:1px solid var(--line2);color:var(--text);border-radius:10px;padding:8px 12px;font:inherit}
.qkD{padding:10px 6px;border-top:1px solid var(--line);cursor:pointer}.qkD:first-child{border-top:0}
.qkCl{display:flex;gap:10px;align-items:center;padding:8px 4px;border-top:1px solid var(--line)}
.qkTw{position:relative;width:150px;height:75px;flex:none;border-radius:7px;background:#0b0f14;display:inline-flex;align-items:center;justify-content:center;overflow:hidden}
.qkTw::before{content:"畫面讀取中…";position:absolute;color:var(--sub);font-size:11px}.qkTh{position:relative;width:150px;height:75px;object-fit:cover;cursor:zoom-in;opacity:0}
.qkMv{flex:none;background:none;border:1px solid var(--line2);color:#ffb347;border-radius:8px;padding:4px 8px;font:inherit}
.qkPb{border-color:#5a4520}.qkCatH{color:#ffb347;font-weight:800;margin:10px 0 2px}.qkWarn{color:#ffb347;font-size:20px;flex:none;width:22px;text-align:center}
.qkPbB{display:flex;flex-direction:column;gap:6px;flex:none}.qkPbB button{background:var(--btn);border:1px solid var(--line2);color:var(--text);border-radius:8px;padding:5px 9px;font:inherit;font-size:13px}
.qkEst{background:#0f1a24;border:1px solid #1e3346;border-radius:10px;padding:10px;margin-top:10px;line-height:1.6}
@media(max-width:620px){.qk .qkCl{flex-wrap:wrap}.qk .qkCl small{flex:1 1 100%;order:9;text-align:left;padding-left:32px}.qk .qkTw,.qk .qkTh{width:132px;height:66px}.qk .qkMv,.qk .qkPbB{margin-left:auto}}.qkCl input{width:22px;height:22px;flex:none}.qkCl b{white-space:nowrap}.qkCl small{flex:1;text-align:right}.qkCl small{color:var(--sub)}
.qkGo{display:block;width:100%;background:#e51e63;color:#fff;border:0;border-radius:12px;padding:14px;font:inherit;font-weight:900;font-size:17px;margin-top:10px}.qkGo:disabled{opacity:.45}
.qkTip{font-size:13px;margin-top:6px}.qkTip.bad{color:#ff6577;font-weight:800}.qkTip.ok{color:#45d483}.qkTip.info{color:var(--sub)}
.qkJ{padding:8px 0;border-top:1px solid var(--line)}.qkJ:first-child{border-top:0}.qkJt{display:flex;justify-content:space-between;gap:8px}.qkJt em{font-style:normal;color:var(--sub);font-size:13px}.qkJt em.ok{color:#45d483;font-weight:800}
.qkP{display:block;height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:5px 0}.qkP u{display:block;height:100%;background:#2b8ad8}.qkJ small{color:var(--sub)}</style>
<div class="qk">
<div class="qkC"><div class="qkH">① 素材資料夾（電腦上的）</div><div class="qkPath">${esc(d.folder||"尚未選擇")}</div>
<div class="qkRow">${d.parent!=null&&d.folder?`<button data-up="${esc(d.parent)}">⬆ 上一層</button>`:""}<button id="qkPick">選擇其他資料夾…</button>
${d.recent.length?`<select id="qkRec"><option value="">最近用過…</option>${d.recent.map(r=>`<option value="${esc(r)}">${esc(r)}</option>`).join("")}</select>`:""}</div>
${d.dirs.length?`<div style="margin-top:8px">${d.dirs.map(x=>`<div class="qkD" data-go="${esc(x)}">📁 ${nm(x)}</div>`).join("")}</div>`:""}</div>
<div class="qkC"><div class="qkH" style="display:flex;align-items:center;gap:8px">② 影片（${good.length}${QHIDE&&nDone?`，藏了 ${nDone} 支已轉過`:""}）${nDone?`<label style="margin-left:auto;font-weight:400;font-size:13px;color:var(--sub);display:flex;gap:6px;align-items:center"><input type="checkbox" id="qkHide" ${QHIDE?"checked":""}>只看沒轉過的</label>`:""}</div>${good.length?good.map(c=>`<label class="qkCl"><input type="checkbox" data-id="${esc(c.id)}"${QSEL.ids.has(c.id)?" checked":""}${c.complete?"":" disabled"}><b>${esc(c.name)}</b>${qkImg(c)}<small>${esc(c.time||"")} · ${fd(c.duration)} · ${fmtB(c.size)}${c.est?` → 約 ${fmtB(c.est)}`:""} · ${c.done&&!c.status?`<span style="color:#45d483" title="${esc(c.done.how+"："+c.done.path)}">✓ 已轉過</span>`:esc(st(c))}</small><button class="qkMv" data-pm="${esc(c.id)}" title="移到問題檔案（先不轉）">⚠</button></label>`).join(""):'<div class="qkTip info">這裡沒有 GoPro Fusion 影片，請選放素材的資料夾</div>'}</div>
${probs.length?`<div class="qkC qkPb"><div class="qkH" style="color:#ffb347">⚠ 問題檔案（${probs.length}）</div><div class="qkTip info">這些先不轉。原始檔案沒有被移動，只是集中在這裡，之後再分析。</div>${qkCats(probs).map(([lab,cs])=>`<div class="qkCatH">${esc(lab)}（${cs.length}）</div>`+cs.map(c=>`<div class="qkCl"><span class="qkWarn">⚠</span><b>${esc(c.name)}</b>${c.pair==="ok"?qkImg(c):""}<small>${esc(c.problem.detail||"")}<br>${esc(c.time||"")} · ${fd(c.duration)} · ${fmtB(c.size)}</small><span class="qkPbB"><button data-pc="${esc(c.id)}">重新讀取</button><button data-pr="${esc(c.id)}">移回清單</button></span></div>`).join("")).join("")}</div>`:""}
<div class="qkC"><div class="qkH">③ 轉好的影片放在</div>
<div class="qkRow"><select id="qkMode"><option value="beside"${d.out_mode!=="custom"?" selected":""}>素材資料夾裡的 stitched</option><option value="custom"${d.out_mode==="custom"?" selected":""}>固定的資料夾</option></select>${d.out_mode==="custom"?'<button id="qkOut">選擇…</button>':""}</div>
<div class="qkPath">${esc(d.out||"")}</div><div class="qkTip ${esc(d.level)}">${esc(d.tip)}</div>
<div class="qkRow" style="margin-top:8px"><button id="qkSsd" style="${d.relay_on?"background:#1d5c3a;color:#fff;border:0;font-weight:800":""}">SSD 加速：${d.relay_on?"開":"關"}</button><span class="qkTip info">${esc(d.relay)}</span></div>
<div class="qkRow" style="margin-top:8px"><span class="qkTip info">輸出格式</span><select id="qkPre">${d.presets.map(p=>`<option value="${esc(p.key)}"${p.key===d.preset?" selected":""}>${esc(p.label)}</option>`).join("")}</select></div>
<div id="qkEst">${qkEst(d)}</div><button class="qkGo" id="qkGo"${sel?"":" disabled"}>開始轉檔${sel?`（${sel} 個）`:""}</button></div>
<div class="qkC"><div class="qkH">④ 轉檔中</div><div id="qkJobs">${fin}${jobs||'<div class="qkTip info">目前沒有轉檔工作</div>'}</div></div></div>`;
  const open=async p=>{await act("/api/quick/open",{path:p});S.qk=null;S.qkSig="";QSEL=null;setTimeout(viewQuick,700);};
  host.onclick=async e=>{const t=e.target;const g=t.closest("[data-go]");if(g){open(g.dataset.go);return;}const u=t.closest("[data-up]");if(u){open(u.dataset.up);return;}
    if(t.id==="qkPick"){pickDir("選擇素材資料夾",d.folder,p=>open(p));return;}
    if(t.id==="qkOut"){pickDir("選擇成品資料夾",d.out_fixed||"",async p=>{await act("/api/quick/out",{mode:"custom",path:p});viewQuick();});return;}
    if(t.id==="qkSsd"){await act("/api/quick/ssd",{on:!d.relay_on});viewQuick();return;}
    const pb=t.closest("[data-pm],[data-pr],[data-pc]");if(pb){e.preventDefault();e.stopPropagation();const id=pb.dataset.pm||pb.dataset.pr||pb.dataset.pc;const a=pb.dataset.pm?"mark":(pb.dataset.pr?"release":"recheck");
      await act("/api/problem",{id,action:a});QSEL.ids.delete(id);S.qkSig="";setTimeout(viewQuick,a==="recheck"?2500:300);return;}
    if(t.id==="qkGo"){const ids=[...QSEL.ids].filter(i=>good.some(c=>c.id===i));await act("/api/library/queue",{ids,preset:$("#qkPre").value});QSEL.touched=true;QSEL.ids.clear();setTimeout(viewQuick,800);return;}
    if(t.matches("input[type=checkbox][data-id]")){QSEL.touched=true;t.checked?QSEL.ids.add(t.dataset.id):QSEL.ids.delete(t.dataset.id);const n=good.filter(c=>QSEL.ids.has(c.id)).length;const b=$("#qkGo");b.disabled=!n;b.textContent="開始轉檔"+(n?`（${n} 個）`:"");const es=$("#qkEst");if(es)es.innerHTML=qkEst(d);}};
  host.onchange=async e=>{const t=e.target;if(t.id==="qkRec"&&t.value){open(t.value);}
    if(t.id==="qkMode"){if(t.value==="custom"&&!d.out_fixed){pickDir("選擇成品資料夾","",async p=>{await act("/api/quick/out",{mode:"custom",path:p});viewQuick();});}else{await act("/api/quick/out",{mode:t.value,path:d.out_fixed});viewQuick();}}};
  later(viewQuick,3000);}
/* ================= 素材庫 ================= */
const LIBF=[["all","全部"],["new","未轉檔"],["done","已轉檔"],["q","佇列中"],["bad","失敗／損壞"],["ed","已調整"]];
async function viewLib(){$("#clk").textContent="";const host=V();if(!S.lib)host.innerHTML='<div class="empty">讀取中…</div>';
  let d;try{d=await J("/api/library");}catch(e){if(!S.lib)host.innerHTML=offline;later(viewLib,8000);return;}S.lib=d;if(!host.isConnected||location.hash.slice(1).split("/")[0]!=="lib"&&location.hash)return;
  const f=lsGet("fs_libf","all"),srt=lsGet("fs_libs","num"),q=(S.libq||"").trim().toLowerCase();
  let cs=d.clips.filter(c=>(f==="all"||(f==="new"&&c.status==="")||(f==="done"&&c.status==="已轉檔")||(f==="q"&&/轉檔中|佇列中|已暫停/.test(c.status))||(f==="bad"&&/失敗|⚠|未完成|不完整/.test(c.status+(c.complete?"":"不完整")))||(f==="ed"&&c.edited))&&(!q||(c.name+c.time).toLowerCase().includes(q)));
  const key={num:c=>c.num,time:c=>c.time,dur:c=>-c.duration,size:c=>-c.size}[srt]||(c=>c.num);cs.sort((a,b)=>key(a)<key(b)?-1:key(a)>key(b)?1:0);
  const cnt=k=>d.clips.filter(c=>k==="new"?c.status==="":k==="done"?c.status==="已轉檔":0).length;
  let h=`<div class="stk"><div class="tools"><input class="search" id="lq" placeholder="🔎 搜尋素材（編號、日期）" value="${esc(S.libq||"")}"><button class="pill" id="pick" style="flex:none">📁 換資料夾</button><button class="pill" id="rescan" style="flex:none">⟳</button></div>
<div class="tools"><div class="seg" id="lf" style="flex:1 1 auto;min-width:0">${LIBF.map(([k,t])=>`<button data-k="${k}"${k===f?' class="on"':""}>${t}</button>`).join("")}</div>
</div><div class="tools"><select class="pill" id="ls" style="flex:0 1 112px">${[["num","依編號"],["time","依拍攝時間"],["dur","依長度"],["size","依大小"]].map(([k,t])=>`<option value="${k}"${k===srt?" selected":""}>${t}</option>`).join("")}</select><select class="pill" id="lp" title="轉檔預設">${d.presets.map(p=>`<option value="${esc(p.key)}"${p.key===d.preset?" selected":""}>${esc(p.label)}</option>`).join("")}</select></div>
<div class="g2" style="margin-top:6px"><button class="pill" id="q1">🎯 全部未轉 → GoPro 4K</button><button class="pill" id="q2">✦ 全部未轉 → 全自動</button></div>
<div class="snote">📁 ${esc(d.folder||"尚未選擇素材資料夾")} · 顯示 ${cs.length} / ${d.clips.length} · 未轉檔 ${cnt("new")} · 已轉檔 ${cnt("done")}</div></div>`;
  h+=cs.map(c=>{const o=Object.entries(c.outputs);const cls=c.status==="已轉檔"?"live":/失敗|⚠|不完整/.test(c.status)?"bad":/轉檔中/.test(c.status)?"run":/佇列|暫停/.test(c.status)?"wait":"";
    const out=o.length?o.map(([n,s])=>`<span class="chip sz">成品 ${fmtB(s)}${c.size?" · "+Math.round(s/c.size*100)+"%":""}</span>`).join(""):(c.estimate?`<span class="chip t">預估 ≈ ${fmtB(c.estimate)}</span>`:"");
    return `<div class="c ${cls}" data-id="${esc(c.id)}"><div class="cb"><input type="checkbox" class="ck" data-ck="${esc(c.id)}"${S.sel.has(c.id)?" checked":""}>${c.thumb?`<img class="th" loading="lazy" src="${B}/api/clipthumb?id=${encodeURIComponent(c.id)}">`:'<div class="th"></div>'}
<div class="ci"><div class="r1"><span class="nm">${esc(c.name)}</span><span class="chip ${cls||"t"}">${esc(c.status||"未轉檔")}</span>${c.edited?'<span class="chip t">✎ 已調整</span>':""}${c.complete?"":'<span class="chip bad">前後不完整</span>'}</div>
<div class="r2"><span>${esc(c.time)}</span><span>${fd(c.duration)}</span><span>${c.kind==="video"?c.chapters+" 章節":"照片"}</span><span>素材 ${fmtB(c.size)}</span>${c.wav?"<span>🎙 360</span>":""}</div><div class="r2">${out}</div></div></div>
${c.complete?`<div class="acts"><button class="pill" data-q="${esc(c.id)}">${o.length?"↻ 重新轉檔":"▶ 轉檔"}</button><button class="pill" data-g="${esc(c.id)}">🎯 GoPro 4K</button>${c.kind==="video"?`<button class="pill" data-a="${esc(c.id)}">✦ 全自動</button>`:""}${o.length?`<button class="pill" data-play="${esc(o[0][0])}">▶ 播放</button>`:""}</div>`:""}</div>`;}).join("")||'<div class="empty">沒有符合的素材</div>';
  h+=`<div class="selbar" id="selb"${S.sel.size?"":" hidden"}><span>已選 ${S.sel.size} 段</span><button class="btn s" id="bq">加入佇列</button><button class="btn s" id="bg">🎯 GoPro 4K</button><button class="btn" id="ba">✦ 全自動</button></div>`;
  const y=window.scrollY;host.innerHTML=h;window.scrollTo(0,y);
  const pr=()=>$("#lp").value;const on=$("#lf .on");if(on)$("#lf").scrollLeft=Math.max(0,on.offsetLeft-40);
  $("#lq").oninput=e=>{if(e.isComposing)return;S.libq=e.target.value;clearTimeout(S.lqT);S.lqT=setTimeout(viewLib,300);};
  $("#lq").addEventListener("compositionend",e=>{S.libq=e.target.value;viewLib();});
  $("#lf").onclick=e=>{const b=e.target.closest("button");if(!b)return;lsSet("fs_libf",b.dataset.k);viewLib();};
  $("#ls").onchange=e=>{lsSet("fs_libs",e.target.value);viewLib();};
  $("#pick").onclick=()=>pickDir("選擇素材資料夾",d.folder,async p=>{await act("/api/library/scan",{folder:p});setTimeout(viewLib,2500);});
  $("#rescan").onclick=async()=>{await act("/api/library/scan",{});setTimeout(viewLib,2500);};
  $("#q1").onclick=async()=>{if(confirm("全部未轉檔的素材用「GoPro 對標 4K」加入佇列？")){await act("/api/library/queue_new",{preset:"gopro4k"});viewLib();}};
  $("#q2").onclick=async()=>{if(confirm("全部未轉檔的影片全自動處理？\n\n會自動對齊、防震、判斷方向、白平衡與調色，再轉出。")){await act("/api/library/auto",{ids:[]});viewLib();}};
  host.onclick=async e=>{const ck=e.target.closest("[data-ck]");if(ck){ck.checked?S.sel.add(ck.dataset.ck):S.sel.delete(ck.dataset.ck);const sb=$("#selb");sb.hidden=!S.sel.size;sb.querySelector("span").textContent=`已選 ${S.sel.size} 段`;return;}
    const b=e.target.closest("button");if(!b)return;
    if(b.dataset.q){await act("/api/library/queue",{ids:[b.dataset.q],preset:pr()});viewLib();}
    else if(b.dataset.g){await act("/api/library/queue",{ids:[b.dataset.g],preset:"gopro4k"});viewLib();}
    else if(b.dataset.a){await act("/api/library/auto",{ids:[b.dataset.a]});viewLib();}
    else if(b.dataset.play){S.play=b.dataset.play;location.hash="outs";}
    else if(b.id==="bq"){await act("/api/library/queue",{ids:[...S.sel],preset:pr()});S.sel.clear();viewLib();}
    else if(b.id==="bg"){await act("/api/library/queue",{ids:[...S.sel],preset:"gopro4k"});S.sel.clear();viewLib();}
    else if(b.id==="ba"){if(confirm(`全自動處理選取的 ${S.sel.size} 段？`)){await act("/api/library/auto",{ids:[...S.sel]});S.sel.clear();viewLib();}}};
  later(viewLib,8000);}
/* ================= 轉檔佇列 ================= */
let CUT=null;
async function cuMount(box){if(!box)return;clearInterval(CUT);
  const draw=async()=>{if(!box.isConnected){clearInterval(CUT);return;}let S;try{S=await J("/api/cloud");}catch(e){return;}
    const c=S.chk||{};let h=`<h3>☁ 雲端更新</h3><div class="snote" style="margin:0 0 6px">目前 V${esc(S.cur)}（啟動器第 ${S.launcher} 版）。有新版只提示，按「更新」才下載（背景、不限速）。</div>`;
    if(S.dl){const d=S.dl,p=d.size?d.got/d.size*100:0;h+=`<div>⬇ 下載 ${esc(d.label)}：<b>${p.toFixed(0)}%</b>（${fmtB(d.got)} / ${fmtB(d.size||0)}，${fmtB(d.spd||0)}/s）</div><i class="pb"><u style="width:${p.toFixed(1)}%"></u></i>`;}
    else if(S.msg)h+=`<div>${esc(S.msg)}</div>`;
    if(c.newer){h+=`<div style="margin-top:6px">🆕 <b>有新版 V${esc(S.ver)}</b>${S.date?`（${esc(S.date)}）`:""}　${c.need_shell?'<b style="color:#ffb347">⚠ 這次要換 EXE：程式會關掉再自動打開（約 1～2 分鐘），佇列從中斷處接著轉</b>':'熱更新，轉檔不中斷'}${c.skipped?" · 已略過這版":""}</div>`;
      if(S.notes&&S.notes.length)h+=`<ul style="margin:4px 0 0 18px;padding:0">${S.notes.slice(0,8).map(n=>`<li>${esc(n)}</li>`).join("")}</ul>`;
      if(c.need_shell&&S.busy_jobs&&S.busy_jobs.length)h+=`<div class="bwarn" style="margin-top:6px">現在有 ${S.busy_jobs.length} 支轉檔中／排隊：${S.busy_jobs.slice(0,10).map(b=>"#"+b.id+" "+esc(b.title)).join("、")}（建議等轉完再更新）</div>`;}
    else if(c.t)h+=`<div style="margin-top:4px">${esc(c.message||(c.ok?"✓ 已經是最新版":""))}　<span class="snote">（${fmtClk(c.t)} 檢查）</span></div>`;
    h+=`<label style="display:flex;gap:8px;align-items:center;margin-top:8px"><input type="checkbox" id="cuOn"${S.on?" checked":""}> 自動檢查更新（開程式 1 分鐘後、之後每 6 小時）</label>`;
    h+=`<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px"><button class="pill" data-cu="check">☁ 檢查更新</button>${c.newer&&!S.busy?`${c.need_shell?"":`<button class="pill" data-cu="skip">${c.skipped?"已略過":"略過這版"}</button>`}<button class="pill" data-cu="update" style="background:#1d5c3a;border-color:#2e8b57;color:#fff">更新到 V${esc(S.ver)}</button>`:""}${S.dl?'<button class="pill" data-cu="cancel">取消下載</button>':""}</div>`;
    box.innerHTML=h;};
  box.onclick=async e=>{const b=e.target.closest("[data-cu]");if(!b)return;const a=b.dataset.cu;
    if(a==="update"){let r=await post("/api/cloud/update",{});if(r&&r.need_confirm){if(!confirm(r.message+"\n\n現在就更新？"))return;r=await post("/api/cloud/update",{force:true});}if(r&&r.message)toast(r.message);}
    else{const r=await post("/api/cloud/"+a,{});if(r&&r.message)toast(r.message);}
    setTimeout(draw,a==="check"?2500:400);};
  box.onchange=async e=>{if(e.target.id==="cuOn"){const r=await post("/api/cloud/on",{on:e.target.checked});if(r&&r.message)toast(r.message);}};
  await draw();CUT=setInterval(draw,2000);}
let BSEL=new Set();
document.addEventListener("input",e=>{if(e.target&&e.target.id==="pcR"){const v=document.getElementById("pcV");if(v)v.textContent=e.target.value+"%";}});
document.addEventListener("change",async e=>{if(e.target&&e.target.id==="pcR"){await act("/api/pace",{pct:+e.target.value});viewQueue();}});
document.addEventListener("change",async e=>{const t=e.target;if(!t||!t.dataset)return;
  if(t.id==="hsR"){const v=+t.value*10;await act("/api/hs",{share:v});viewQueue();}
  else if(t.id==="hsRes"){await act("/api/hs",{reserve:+t.value});viewQueue();}
  else if(t.id==="hsOn"){await act("/api/hs",{on:t.checked});viewQueue();}
  else if(t.id==="hsResOn"){await act("/api/hs",{reserve_on:t.checked});viewQueue();}
  else if(t.id==="hsPr"){await act("/api/hs",{prio:+t.value});viewQueue();}});
document.addEventListener("input",e=>{const t=e.target;if(t&&t.id==="hsR"){const o=document.getElementById("hsV");if(o)o.textContent=`FusionStitch ${t.value*10} : ${100-t.value*10} ${o.dataset.o}`;}
  if(t&&t.id==="hsRes"){const o=document.getElementById("hsRV");if(o)o.textContent=t.value+"%";}});
document.addEventListener("click",async e=>{const b=e.target.closest&&e.target.closest("[data-hsb]");if(!b)return;await act("/api/hs",{boost:+b.dataset.hsb});viewQueue();});
function aftH(a){if(!a)return "";const cd=a.countdown!=null;
  return `<div class="pcbox"${cd?' style="border-color:#ff6b6b"':""}><div class="pchd"><b>🧪 全部轉完後</b><select id="aftSel" style="margin-left:auto">${[["none","不動作"],["sleep","睡眠"],["shutdown","關機"]].map(([k,t])=>`<option value="${k}" ${a.mode===k?"selected":""}>${t}</option>`).join("")}</select></div>
${cd?`<div style="color:#ff6b6b;font-weight:700;margin:6px 0">⏳ ${a.countdown} 秒後${esc(a.txt)}　<button class="pill" data-aftx="1">取消</button></div>`:`<div class="snote">${a.mode==="none"?"佇列做完不會怎樣":`還剩 ${a.left} 支，全部做完會倒數 60 秒再${esc(a.txt)}（做一次就回到不動作）`}</div>`}</div>`;}
document.addEventListener("change",e=>{if(e.target&&e.target.id==="qkHide"){try{localStorage.setItem("fsQHide",e.target.checked?"1":"0");}catch(x){}S.qkSig=null;viewQuick();}});
function finH(f){if(!f)return "";return `<div class="pcbox" style="padding:8px 12px"><b>⏱ 預計 ${fmtClk(f.at)} 全部完成</b> <span class="snote">（剩 ${fd(f.at-Date.now()/1000)}、${f.n} 支${f.partial?"，有幾支還沒有預估":""}）</span></div>`;}
document.addEventListener("change",async e=>{if(e.target&&e.target.id==="aftSel"){await act("/api/after",{mode:e.target.value});viewQueue();}});
document.addEventListener("click",async e=>{if(e.target.closest&&e.target.closest("[data-aftx]")){await act("/api/after",{cancel:true});viewQueue();}});
function hsH(h){if(!h)return "";const M={alone:"照常跑",lead:"這邊先跑",yield:"讓對方先",split:"照分配"};const P=h.peers||[];const o=h.other||"SortCut";
  const who=P.length?P.map(p=>`${esc(p.name)} V${esc(p.ver)}（${esc(p.what||"閒著")}）`).join("、"):"沒看到 "+esc(o);
  const left=h.boost_until-Date.now()/1000;const sh=Math.round((h.share||50)/10);
  return `<div class="pcbox"><div class="pchd"><b>🤝 握手（SortCut、ComfyRemote）</b><label style="display:flex;gap:6px;align-items:center;margin-left:auto"><input type="checkbox" id="hsOn" ${h.on?"checked":""}>開</label></div>
${h.on?`<div class="snote" style="margin:2px 0 6px;${h.mode==="yield"||h.mode==="split"?"color:#ffb347":""}">${who}<br>${M[h.mode]||h.mode}：${esc(h.why||"")} · 規則第 ${h.rules} 版${h.rules_from==="lib"?"（對方的新版）":""}</div>
<div class="pcs"><span>分配</span><input type="range" id="hsR" min="1" max="9" step="1" value="${sh}"><b id="hsV" data-o="${esc(o)}" style="white-space:nowrap">FusionStitch ${sh*10} : ${100-sh*10} ${esc(o)}</b></div>
<div class="pcs"><label style="display:flex;gap:6px;align-items:center;white-space:nowrap"><input type="checkbox" id="hsResOn" ${h.reserve_on?"checked":""}>留給你</label><input type="range" id="hsRes" min="0" max="80" step="10" value="${h.reserve}"><b id="hsRV">${h.reserve}%</b></div>
<div class="pcs"><span>優先</span><select id="hsPr">${[0,1,2,3,4,5,6,7,8,9,10].map(v=>`<option${v===h.prio?" selected":""}>${v}</option>`).join("")}</select>
${left>0?`<button class="pill" data-hsb="0">取消先跑（剩 ${Math.ceil(left/60)} 分）</button>`:`<button class="pill" data-hsb="1">⏩ 這邊先跑 1 小時</button>`}</div>
<div class="snote" style="margin:4px 0 0">Lv ${h.lv}。有工作就全力跑；你在用電腦時大家合起來最多 ${100-h.reserve}%；FusionStitch、SortCut 都在忙時照分配；${esc((h.boss||[]).join("、"))} 真的在跑時它 ${Math.round(h.boss_cap||80)}%、其他 ${Math.round(h.others_cap||5)}%。更多設定（誰參與）點右上角 Lv。</div>`:`<div class="snote">關著：兩邊各跑各的</div>`}</div>`;}
function paceH(p){const M=[["full","🚀 全速"],["smart","🧠 智慧降速"],["manual","🐢 固定降速"]];
  return `<div class="pcbox"><div class="pchd"><b>⚡ 轉檔速度</b><span class="pcnow ${p.level<0.999?"slow":""}">${esc(p.label)}</span></div>
<div class="pcm">${M.map(([k,t])=>`<button class="pill${p.mode===k?" on":""}" data-pmode="${k}">${t}</button>`).join("")}</div>
${p.mode!=="full"?`<div class="pcs"><span>降到</span><input type="range" id="pcR" min="10" max="100" step="5" value="${p.pct}"><b id="pcV">${p.pct}%</b>${[80,60,40,20].map(v=>`<button class="pill" data-pp="${v}">${v}%</button>`).join("")}</div>
<div class="snote" style="margin:4px 0 0">${p.mode==="smart"?`有人在用這台電腦（鍵盤、滑鼠）時降到 ${p.pct}%，${p.idle_min} 分鐘沒動就自動回到全速。`:`一直用 ${p.pct}% 的速度轉檔。`}轉檔不會停，只是放慢；看 YouTube、用其他程式會比較順。</div>`:""}</div>`;}
function ovH(j,p){const o=j.ov;let q=p,t="";
  if(o&&j.status!=="done"){q=o.pct*100;const PH={copy:"等素材複製到 SSD",prep:"準備（對齊、分析白平衡、接縫）",encode:`轉檔 ${(j.progress*100).toFixed(1)}%`,finish:"收尾（合併、寫入 360 資訊）",wait:""};
    t=(PH[o.phase]||"")+(o.left!=null&&o.phase!=="wait"?` · 全部約剩 ${fd(o.left)}`:"");}
  return `<div class="cvl"><span>🎞 整體</span><i class="pb"><u style="width:${q.toFixed(1)}%"></u></i><b>${q.toFixed(1)}%</b></div>${t?`<div class="cpx">${t}</div>`:""}`;}
function cpH(c){if(!c)return "";const p=c.total?c.done/c.total*100:0;
  if(c.state==="done")return `<div class="cvl cpd"><span>📥 中繼 SSD</span><em>已複製好 ✓（${fmtB(c.total)}）</em></div>`;
  return `<div class="cvl cp"><span>📥 複製到 SSD</span><i class="pb"><u style="width:${p.toFixed(1)}%"></u></i><b>${p.toFixed(0)}%</b></div>
<div class="cpx">${c.state==="copying"?`${fmtB(c.done)} / ${fmtB(c.total)}${c.speed>0?` · <b>${fmtB(c.speed)}/s</b> · 約 ${fd((c.total-c.done)/c.speed)}`:" · 計算速度中…"}`:(c.done>0?`已複製 ${fmtB(c.done)} / ${fmtB(c.total)}（其他鏡頭檔等前面的複製完就繼續）`:`等待複製（${fmtB(c.total)}，前面的複製完就開始）`)}</div>`;}
function bcolGet(){try{return new Set(JSON.parse(localStorage.getItem("fs_bcol")||"[]"));}catch(e){return new Set();}}
function bcolSet(c){try{localStorage.setItem("fs_bcol",JSON.stringify([...c].slice(-300)));}catch(e){}}
function batchSheet(ids,d){if(!ids.length)return;const bs=d.batches.filter(b=>ids.includes(b.id));const one=bs.length===1?bs[0]:null;
  const ov=document.createElement("div");ov.className="calov";const same=k=>bs.every(b=>JSON.stringify(b[k])===JSON.stringify(bs[0][k]));let cur={dest:same("dest")?bs[0].dest:"",spares:same("spares")?[...(bs[0].spares||["",""])]:["",""]};while(cur.spares.length<2)cur.spares.push("");
  const row=(k,lab,val,hint)=>`<div class="bsr"><div class="bsl">${lab}</div><div class="bsv">${val?esc(val):`<span style="color:var(--sub)">${hint}</span>`}</div><div class="bsb"><button class="pill" data-pick="${k}">選擇…</button>${val?`<button class="pill" data-x="${k}" title="清除">✕</button>`:""}</div></div>`;
  const draw=()=>{ov.innerHTML=`<div class="cal"><div class="calhd"><b>⚙ 批次設定</b><button class="calx">✕</button></div><div class="calbody" style="padding:10px 12px">
<style>.bsr{display:flex;gap:8px;align-items:center;padding:10px 0;border-top:1px solid var(--line)}.bsl{width:78px;flex:none;color:var(--sub);font-size:13.5px}.bsv{flex:1;min-width:0;word-break:break-all;color:var(--strong);font-size:14px}.bsb{display:flex;gap:6px;flex:none}</style>
<div class="snote" style="margin:0 0 6px">套用到：${bs.map(b=>"📁 "+esc(b.title)).join("、")}</div>
${one||bs.every(b=>b.dest===bs[0].dest)?row("dest","成品資料夾",cur.dest,"預設："+esc(bs[0].dest_dir||"素材資料夾裡的 stitched")):row("dest","成品資料夾","","（各批次不同，選了就全部改成同一個）")}
${row("s0","備用 1",cur.spares[0],"未設定")}${row("s1","備用 2",cur.spares[1],"未設定")}
<div class="snote" style="margin-top:8px">• 改成品資料夾：正在轉的那支不變，之後排隊的會存到新位置。<br>• 成品資料夾放不下時，自動改存到備用 1，再放不下存備用 2；都沒有空間才暫停。<br>• 新加入的批次會沿用上一批的備用資料夾。</div></div>
<div class="calft"><span class="calsel"></span><button class="calok" id="bsOk">完成</button></div></div>`;};
  const send=async patch=>{const j=await act("/api/batch/set",Object.assign({ids},patch));if(j&&j.ok!==false){Object.assign(cur,patch.dest!==undefined?{dest:patch.dest}:{},patch.spares?{spares:patch.spares}:{});draw();}};
  draw();document.body.appendChild(ov);const close=b=>{ov.remove();if(b!==true)vgBackDone(close);viewQueue();};vgBackPush(close);
  ov.onclick=e=>{if(e.target===ov||e.target.closest(".calx")||e.target.closest("#bsOk")){close();return;}const b=e.target.closest("button");if(!b)return;
    const k=b.dataset.pick||b.dataset.x;if(!k)return;const clear=!!b.dataset.x;
    const apply=v=>{if(k==="dest")send({dest:v});else{const sp=[...cur.spares];sp[k==="s0"?0:1]=v;send({spares:sp});}};
    if(clear){apply("");return;}
    const start=k==="dest"?(cur.dest||bs[0].dest_dir||""):(cur.spares[k==="s0"?0:1]||"");
    pickDir(k==="dest"?"成品資料夾":"備用資料夾"+(k==="s0"?" 1":" 2"),start,v=>apply(v));};}
async function viewQueue(){const host=V();if(!S.q)host.innerHTML='<div class="empty">讀取中…</div>';
  let d;try{d=await J("/api/queue");}catch(e){if(!S.q)host.innerHTML=offline;later(viewQueue,3000);return;}S.q=d;if(!host.isConnected)return;
  const run=d.jobs.find(j=>j.status==="running");$("#clk").textContent=run?`▶ ${run.fps.toFixed(1)} fps · 剩 ${fd(run.eta)}`:"閒置";
  const A=[["none","不做任何事"],["sleep","睡眠"],["shutdown","關機"]];
  let h=`<div class="stk"><div class="g2"><button class="pill" data-qa="pause" style="text-align:center">⏸ 全部暫停</button><button class="pill" data-qa="resume" style="text-align:center">▶ 全部繼續</button><button class="pill" data-qa="retry" style="text-align:center">↻ 失敗全部重試</button><button class="pill" data-qa="clear" style="text-align:center">清除已完成</button></div>
<div class="tools" style="margin-top:6px"><span class="snote" style="margin:0;flex:none">全部轉完後</span><div class="seg" id="qafter">${A.map(([k,t])=>`<button data-k="${k}"${k===d.after?' class="on"':""}>${t}</button>`).join("")}</div></div>
<div class="snote">${run?`▶ #${run.id} ${esc(run.title)} ${(run.progress*100).toFixed(1)}% · ${run.fps.toFixed(1)} fps · 剩 ${fd(run.eta)}`:"閒置"} · 排隊 ${d.jobs.filter(j=>j.status==="queued").length}</div></div>
<div class="panel">
${finH(d.all_finish)}${d.pace?paceH(d.pace):""}${hsH(d.hs)}${aftH(d.after)}<div class="r2"><span>💽 中繼 SSD</span><b style="color:var(--strong);flex:1;min-width:0;word-break:break-all">${esc(d.cache_dir||"不使用")}</b><button class="pill" id="pc" style="flex:none">選擇…</button>${d.cache_dir?'<button class="pill" id="pcx" style="flex:none">不使用</button>':""}</div>
${d.cache_dir?`<div class="r2" style="margin-top:6px"><span>💽 中繼 2（備援）</span><b style="color:var(--strong);flex:1;min-width:0;word-break:break-all">${esc(d.cache_dir2||"不使用")}</b><button class="pill" id="pc2" style="flex:none">選擇…</button>${d.cache_dir2?'<button class="pill" id="pc2x" style="flex:none">不使用</button>':""}</div>`:""}
<div class="r2" style="margin-top:6px"><span>📦 成品送到</span><b style="color:var(--strong);flex:1;min-width:0;word-break:break-all">${esc(d.dest||"素材資料夾裡的 stitched")}</b><button class="pill" id="pd" style="flex:none">選擇…</button>${d.dest?'<button class="pill" id="pdx" style="flex:none">回預設</button>':""}</div>
${d.relay_use?`<div class="snote">📥 中繼已先存素材：${(d.relay_use.roots||[{drive:"",used:d.relay_use.used,cap:d.relay_use.cap,main:true}]).map(x=>`${esc(x.drive)}${x.main?"（主要）":"（備援）"} ${fmtB(x.used)}${x.cap?" / "+fmtB(x.cap):""}`).join("　")}（正在轉的和下一支一定先存，其他滿了就等）</div>`:""}${d.relay?`<div class="snote">${esc(d.relay)}</div>`:""}</div>`;
  const JC=(j,b)=>{const s=ST[j.status]||[j.status,"t"],p=(j.status==="done"?1:j.progress)*100,stx=j.retry_in?`⚠ ${j.retry_in} 秒後重試`:s[0];
      const jf=b&&b.job_finish[j.id],js=b&&b.job_size[j.id];
      return `<div class="c ${j.status==="running"?"run":j.status==="error"?"bad":j.retry_in?"wait":""}"><div class="r1"><span class="nm">#${j.id} ${esc(j.title)}</span><span class="chip ${j.retry_in?"warn":s[1]}">${stx}</span></div>
<div class="r2"><span>${(j.ov&&j.status!=="done"?j.ov.pct*100:p).toFixed(1)}%</span>${j.status==="running"?`<span>${j.fps.toFixed(1)} fps</span><span>剩 ${fd(j.ov&&j.ov.left!=null?j.ov.left:j.eta)}</span><span>${esc(j.detail)}</span>`:""}${jf&&j.status==="queued"?`<span>預計 ${fmtClk(jf)} 完成</span>`:""}${js?`<span>${j.status==="done"?"":"約 "}${fmtB(js)}</span>`:""}<span style="word-break:break-all">${esc(j.name)}</span></div>
${cpH(j.copy)}${ovH(j,p)}${j.message&&!(j.copy&&j.copy.state!=="done"&&/^複製素材/.test(j.message))?`<div class="snote" style="margin:4px 0 0">${esc(j.message)}</div>`:""}
<div class="acts">${["running","queued"].includes(j.status)?`<button class="pill" data-j="${j.id}" data-a="pause">⏸ 暫停</button>`:""}${j.status==="paused"?`<button class="pill" data-j="${j.id}" data-a="resume">▶ 繼續</button>`:""}${j.status==="error"?`<button class="pill" data-j="${j.id}" data-a="resume">↻ 重試</button>`:""}
${["queued","paused"].includes(j.status)?`<button class="pill" data-j="${j.id}" data-a="top">⤒ 置頂</button><button class="pill" data-j="${j.id}" data-a="up">↑</button><button class="pill" data-j="${j.id}" data-a="down">↓</button>`:""}
${["running","queued","paused","error"].includes(j.status)?`<button class="pill" data-j="${j.id}" data-a="cancel">✕ 取消</button>`:""}${j.status==="done"&&j.name?`<button class="pill" data-play="${esc(j.name)}">▶ 播放</button>`:""}</div></div>`;};
  const col=bcolGet(),byId={};d.jobs.forEach(j=>byId[j.id]=j);
  BSEL=new Set([...BSEL].filter(x=>d.batches.some(b=>b.id===x)));
  h+=`<style>.bt{border:1px solid var(--line);border-radius:14px;margin:10px 0;background:var(--card);overflow:hidden}.bt.run{border-color:#2b6a9c}
.bth{display:flex;gap:8px;align-items:center;padding:10px 10px 4px;cursor:pointer}.bth b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--strong);font-size:15.5px}
.bth input{width:20px;height:20px;flex:none}.btar{color:var(--sub);flex:none;transition:transform .15s}.bt.cl .btar{transform:rotate(-90deg)}
.bti{padding:0 10px 10px}.bti .pb{margin:6px 0}.bti .r2{flex-wrap:wrap}.bwarn{background:#3a2a10;border:1px solid #6b4a14;color:#ffd18a;border-radius:9px;padding:7px 9px;margin-top:6px;font-size:13px}
.btj{padding:0 6px 6px}.bt.cl .btj{display:none}.bsp{font-size:11.5px;font-weight:800;border-radius:6px;padding:2px 6px;background:#1d5c3a;color:#bff5d6;flex:none}
.bset{flex:none}.pcbox{border:1px solid var(--line);border-radius:12px;padding:10px;margin:0 0 10px;background:#0f1a24}.pchd{display:flex;gap:8px;align-items:center}.pchd b{flex:1}
.pcnow{font-size:12.5px;color:#45d483}.pcnow.slow{color:#ffb347}.pcm{display:flex;gap:6px;margin-top:8px}.pcm .pill{flex:1;text-align:center}.pcm .pill.on{background:#16324a;border-color:#2b8ad8;color:#fff}
.pcs{display:flex;gap:6px;align-items:center;margin-top:8px;flex-wrap:wrap}.pcs input{flex:1;min-width:120px;accent-color:#ffb347}.pcs b{width:42px;text-align:right}.pcs .pill{flex:none;padding:4px 8px}.bacts{display:flex;gap:6px;margin-top:8px}.bacts .pill{flex:1;text-align:center}.bacts .bx{color:#ff6577;border-color:#6b2a33}.cvl{display:flex;gap:8px;align-items:center;margin:5px 0 0}.cvl span{flex:none;width:92px;font-size:12.5px;color:var(--sub)}.cvl .pb{flex:1;margin:0}.cvl b{flex:none;width:44px;text-align:right;font-size:12.5px}
.cvl.cp .pb u{background:#19b3a6}.cpd em{font-style:normal;color:#45d483;font-size:12.5px}.cpx{font-size:12px;color:var(--sub);margin:2px 0 0 100px}.cpx b{color:#5fe0d4}#bselbar{position:sticky;bottom:8px;z-index:20;display:flex;gap:8px;align-items:center;background:#16324a;border:1px solid #2b6a9c;border-radius:12px;padding:8px 10px;margin:8px 0}#bselbar span{flex:1}</style>`;
  if(BSEL.size)h+=`<div id="bselbar"><span>已選 ${BSEL.size} 個批次</span><button class="pill" data-bset="__sel">⚙ 一起設定</button><button class="pill" data-bsx="1" style="color:#ff6577">✕ 取消選取的批次</button><button class="pill" data-bclr="1">取消選取</button></div>`;
  for(const b of d.batches){const L=b.jobs.map(x=>byId[x]).filter(Boolean);if(!L.length)continue;
    const cl=col.has(b.id)||(!col.has("o:"+b.id)&&!b.active);
    const t=b.left!=null&&b.active?`剩 ${fd(b.left)}${b.eta_partial?" 以上":""} · 預計 ${fmtClk(b.finish)} 完成`:(b.active?"完成時間估算中":(b.errors?`${b.errors} 支失敗`:"全部完成"));
    const dv=b.drive;
    const sp=(b.spare_info||[]).map((x,i)=>`<span class="bsp" title="${esc(x.path)}">備用${i+1} ${esc(x.drive.replace("\\",""))}${x.kind?" "+x.kind:""} ✓</span>`).join("");
    h+=`<div class="bt${b.running?" run":""}${cl?" cl":""}" data-b="${esc(b.id)}"><div class="bth" data-bt="${esc(b.id)}"><input type="checkbox" data-bs="${esc(b.id)}"${BSEL.has(b.id)?" checked":""}><span class="btar">▾</span><b>📁 ${esc(b.title)}</b><span class="chip">${b.done}/${b.n}</span>${sp}<button class="pill bset" data-bset="${esc(b.id)}">⚙</button></div>
<div class="bti"><i class="pb"><u style="width:${(b.progress*100).toFixed(1)}%"></u></i>
<div class="r2"><span><b>${(b.progress*100).toFixed(1)}%</b></span><span>${t}</span><span>成品約 ${fmtB(b.size_total)}${b.size_left>1e8&&b.active?`（還要寫入 ${fmtB(b.size_left)}）`:""}</span></div>
<div class="r2"><span style="word-break:break-all">→ ${esc(b.dest_dir||"")}</span>${dv?`<span>${dv.kind?dv.kind+" · ":""}剩 ${fmtB(Math.max(0,dv.free))}${b.active?` · 全部轉完約剩 <b style="color:${dv.after<15e8?"#ff6577":"var(--strong)"}">${dv.after<0?"不足 "+fmtB(-dv.after):fmtB(dv.after)}</b>`:""}</span>`:""}</div>
${b.warn?`<div class="bwarn">${esc(b.warn)}</div>`:""}
${b.active||b.errors?`<div class="bacts">${b.active?`<button class="pill" data-ba="pause" data-bid="${esc(b.id)}">⏸ 整批暫停</button>`:""}<button class="pill" data-ba="resume" data-bid="${esc(b.id)}">▶ 整批繼續</button><button class="pill bx" data-ba="cancel" data-bid="${esc(b.id)}">✕ 取消整批</button></div>`:""}</div>
<div class="btj">${L.map(j=>JC(j,b)).join("")}</div></div>`;}
  if(!d.jobs.length)h+='<div class="empty">佇列是空的：到「素材庫」選素材加入</div>';
  const y=window.scrollY;host.innerHTML=h;window.scrollTo(0,y);
  host.onclick=async e=>{const cb=e.target.closest("input[data-bs]");if(cb){cb.checked?BSEL.add(cb.dataset.bs):BSEL.delete(cb.dataset.bs);viewQueue();return;}
    const hd=e.target.closest("[data-bt]");if(hd&&!e.target.closest("button")){const id=hd.dataset.bt,el=hd.parentNode,c=bcolGet();const nowCl=!el.classList.contains("cl");el.classList.toggle("cl",nowCl);
      c.delete(id);c.delete("o:"+id);c.add(nowCl?id:"o:"+id);bcolSet(c);return;}
    const b=e.target.closest("button");if(!b)return;
    if(b.dataset.qa){if(b.dataset.qa==="clear"||confirm({pause:"全部暫停？",resume:"全部繼續？",retry:"失敗的工作全部重試（從中斷處繼續）？"}[b.dataset.qa]))await act("/api/queue/"+b.dataset.qa,{});viewQueue();return;}
    if(b.dataset.j){if(b.dataset.a==="cancel"&&!confirm("取消這個工作？\n\n已轉好的部分會刪除。"))return;await act(`/api/job/${b.dataset.j}/${b.dataset.a}`,{});viewQueue();return;}
    if(b.dataset.play){S.play=b.dataset.play;location.hash="outs";return;}
    if(b.dataset.bset){const ids=b.dataset.bset==="__sel"?[...BSEL]:[b.dataset.bset];batchSheet(ids,d);return;}
    if(b.dataset.bclr){BSEL.clear();viewQueue();return;}
    if(b.dataset.bsx){if(!confirm(`取消選取的 ${BSEL.size} 個批次裡所有還沒轉完的影片？已完成的成品不受影響。`))return;await act("/api/batch/action",{ids:[...BSEL],action:"cancel"});BSEL.clear();viewQueue();return;}
    if(b.dataset.ba){const bt=d.batches.find(x=>x.id===b.dataset.bid);const nm=bt?bt.title:"";const n=bt?bt.active+bt.errors:0;
      const q={cancel:`取消「${nm}」整批？\n\n${n} 支還沒轉完的影片全部取消（正在轉的那支會停下，已轉好的部分刪除）。已完成的成品不受影響。`,pause:`暫停「${nm}」整批？之後按「整批繼續」會從中斷處接著轉。`,resume:`繼續「${nm}」整批？`}[b.dataset.ba];
      if(!confirm(q))return;await act("/api/batch/action",{ids:[b.dataset.bid],action:b.dataset.ba});viewQueue();return;}
    const k=b.closest("#qafter")&&b.dataset.k;if(k){await act("/api/queue/after",{value:k});viewQueue();return;}
    if(b.id==="pc")pickDir("中繼 SSD 資料夾",d.cache_dir,async p=>{await act("/api/route",{cache_dir:p});viewQueue();});
    if(b.id==="pd")pickDir("成品資料夾",d.dest,async p=>{await act("/api/route",{dest:p});viewQueue();});
    if(b.id==="pcx"){await act("/api/route",{cache_dir:""});viewQueue();}
    if(b.dataset.pmode){await act("/api/pace",{mode:b.dataset.pmode});viewQueue();return;}
    if(b.dataset.pp){await act("/api/pace",{pct:+b.dataset.pp});viewQueue();return;}
    if(b.id==="pc2")pickDir("中繼 SSD 2（備援）",d.cache_dir2,async p=>{await act("/api/route",{cache_dir2:p});viewQueue();});
    if(b.id==="pc2x"){await act("/api/route",{cache_dir2:""});viewQueue();}if(b.id==="pdx"){await act("/api/route",{dest:""});viewQueue();}};
  later(viewQueue,2500);}
/* ================= 成品 ================= */
/* ================= 360 播放器（手機 App 內） ================= */
const P360=(()=>{
const VS="attribute vec2 p;varying vec2 v;void main(){v=p*0.5+0.5;gl_Position=vec4(p,0.0,1.0);}";
const FS=`precision highp float;uniform sampler2D T;uniform mat3 M;uniform float tanH,asp,pk;uniform int mode;varying vec2 v;const float PI=3.14159265;
void main(){vec2 n=v*2.0-1.0;vec3 d;
 if(mode==2){vec2 f=asp>=2.0?vec2(n.x*asp/2.0,n.y):vec2(n.x,n.y*2.0/asp);if(abs(f.x)>1.0||abs(f.y)>1.0){gl_FragColor=vec4(0.0,0.0,0.0,1.0);return;}gl_FragColor=texture2D(T,vec2(f.x*0.5+0.5,0.5-f.y*0.5));return;}
 if(mode==1){vec2 q=vec2(n.x*asp,n.y)*pk;float r=max(length(q),1e-6);float c=2.0*atan(r*0.5);d=M*vec3(sin(c)*q.x/r,-cos(c),-sin(c)*q.y/r);}
 else d=M*normalize(vec3(n.x*tanH*asp,n.y*tanH,-1.0));
 float lon=atan(d.x,-d.z),lat=asin(clamp(d.y,-1.0,1.0));
 gl_FragColor=texture2D(T,vec2(fract(0.5+lon/(2.0*PI)),clamp(0.5-lat/PI,0.0005,0.9995)));}`;
const D2R=Math.PI/180;
const mul=(a,b)=>{const r=new Array(9);for(let i=0;i<3;i++)for(let j=0;j<3;j++)r[i*3+j]=a[i*3]*b[j]+a[i*3+1]*b[3+j]+a[i*3+2]*b[6+j];return r;};
const Rx=a=>{const c=Math.cos(a),s=Math.sin(a);return[1,0,0,0,c,-s,0,s,c];};
const Ry=a=>{const c=Math.cos(a),s=Math.sin(a);return[c,0,s,0,1,0,-s,0,c];};
const Rz=a=>{const c=Math.cos(a),s=Math.sin(a);return[c,-s,0,s,c,0,0,0,1];};
const colMajor=m=>[m[0],m[3],m[6],m[1],m[4],m[7],m[2],m[5],m[8]];
const MODES=[["360 環景",0],["小星球",1],["平面",2]];
let st=null;
function css(){if(document.getElementById("p360css"))return;const s=document.createElement("style");s.id="p360css";s.textContent=`
#p360{position:fixed;inset:0;z-index:10000;background:#000;touch-action:none;user-select:none;-webkit-user-select:none;color:#fff}
#p360 canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
#p360 .p3t,#p360 .p3b{position:absolute;left:0;right:0;display:flex;gap:6px;align-items:center;padding:8px 10px;transition:opacity .25s}
#p360 .p3t{top:0;background:linear-gradient(#000c,#0000);padding-top:max(8px,env(safe-area-inset-top))}
#p360 .p3b{bottom:0;flex-wrap:wrap;background:linear-gradient(#0000,#000d);padding-bottom:max(10px,env(safe-area-inset-bottom))}
#p360.hide .p3t,#p360.hide .p3b{opacity:0;pointer-events:none}
#p360 button{background:#ffffff1f;border:1px solid #ffffff33;color:#fff;border-radius:10px;padding:7px 10px;font:inherit;font-size:14px;min-width:40px}
#p360 button.on{background:#2b8ad8;border-color:#4dc9ff}
#p360 .p3tt{flex:1;min-width:0;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;opacity:.9}
#p360 .p3seg{display:flex;border:1px solid #ffffff33;border-radius:10px;overflow:hidden}#p360 .p3seg button{border:0;border-radius:0;background:#ffffff14}#p360 .p3seg button.on{background:#2b8ad8}
#p360 .p3sk{flex:1 1 100%;display:flex;gap:8px;align-items:center;font-size:12.5px}#p360 input[type=range]{flex:1;accent-color:#e51e63;height:28px}
#p360 .p3msg{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);max-width:86%;background:#000c;border:1px solid #fff3;border-radius:12px;padding:14px;text-align:center;font-size:14px;line-height:1.6}
#p360 .p3hint{position:absolute;left:50%;bottom:120px;transform:translateX(-50%);background:#000a;border-radius:10px;padding:6px 12px;font-size:12.5px;white-space:nowrap;pointer-events:none;transition:opacity .6s}
#p360 .p3sp{flex:1}`;document.head.appendChild(s);}
function open(src,title){close(true);css();
  const ov=document.createElement("div");ov.id="p360";
  ov.innerHTML=`<canvas></canvas><video playsinline webkit-playsinline preload="auto" style="display:none"></video>
<div class="p3t"><button data-k="x">✕</button><span class="p3tt">${esc(title||"")}</span><div class="p3seg">${MODES.map(([t,m])=>`<button data-m="${m}">${t}</button>`).join("")}</div><button data-k="gyro" title="陀螺儀：轉動手機看四周">📱</button><button data-k="reset" title="回到正面">⟲</button></div>
<div class="p3b"><button data-k="play">▶</button><button data-k="b10">«10</button><button data-k="f10">10»</button><button data-k="spd">1×</button><button data-k="mute">🔊</button><span class="p3sp"></span><button data-k="fs">⛶</button>
<div class="p3sk"><span class="tc">0:00</span><input type="range" min="0" max="1000" value="0"><span class="td">0:00</span></div></div>
<div class="p3msg" hidden></div><div class="p3hint">單指拖曳看四周 · 雙指縮放／旋轉 · 點兩下回正面</div>`;
  document.body.appendChild(ov);
  const cv=ov.querySelector("canvas"),vid=ov.querySelector("video"),rng=ov.querySelector("input[type=range]");
  const gl=cv.getContext("webgl",{antialias:false,premultipliedAlpha:false})||cv.getContext("experimental-webgl");
  st={ov,cv,vid,gl,mode:0,yaw:0,pitch:0,roll:0,fov:90,pyaw:0,ppitch:0,pfov:270,gyro:false,gR:null,gyaw:0,ptrs:new Map(),g0:null,
      raf:0,dirty:true,newFrame:false,hideT:0,lastTap:0,seeking:false,speeds:[0.5,1,1.25,1.5,2],spd:1};
  if(!gl){msg("這支手機的瀏覽器不支援 WebGL，無法用 360 播放。");}
  else{
    const sh=(t,s)=>{const o=gl.createShader(t);gl.shaderSource(o,s);gl.compileShader(o);if(!gl.getShaderParameter(o,gl.COMPILE_STATUS))console.error(gl.getShaderInfoLog(o));return o;};
    const pr=gl.createProgram();gl.attachShader(pr,sh(gl.VERTEX_SHADER,VS));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,FS));gl.linkProgram(pr);gl.useProgram(pr);
    const b=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
    const loc=gl.getAttribLocation(pr,"p");gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
    const tx=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,tx);
    [[gl.TEXTURE_MIN_FILTER,gl.LINEAR],[gl.TEXTURE_MAG_FILTER,gl.LINEAR],[gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE],[gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE]].forEach(([k,v])=>gl.texParameteri(gl.TEXTURE_2D,k,v));
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([0,0,0,255]));
    st.u={M:gl.getUniformLocation(pr,"M"),tanH:gl.getUniformLocation(pr,"tanH"),asp:gl.getUniformLocation(pr,"asp"),pk:gl.getUniformLocation(pr,"pk"),mode:gl.getUniformLocation(pr,"mode"),T:gl.getUniformLocation(pr,"T")};
    gl.uniform1i(st.u.T,0);
  }
  vid.src=src;vid.load();
  vid.addEventListener("loadedmetadata",()=>{if(!st)return;ov.querySelector(".td").textContent=fd(vid.duration);st.dirty=true;
    if(vid.videoWidth&&vid.videoWidth/vid.videoHeight<1.8)msg("這支影片不是 2:1 的 360 展開圖，畫面可能不正確（可切到「平面」看）",true);});
  vid.addEventListener("error",()=>st&&msg("手機沒辦法播放這支影片。<br>5.2K 的 H.265 常常超過手機的解碼能力：可以在電腦上用 360 播放，或用「GoPro 4K」設定轉一份給手機看。<br><br><button data-k='plain'>改用一般播放器試試</button>"));
  vid.addEventListener("waiting",()=>{if(st)ov.querySelector('[data-k="play"]').textContent="⏳";});
  vid.addEventListener("playing",()=>{if(!st)return;ov.querySelector('[data-k="play"]').textContent="⏸";hideLater();});
  vid.addEventListener("pause",()=>{if(!st)return;ov.querySelector('[data-k="play"]').textContent="▶";showCtl();});
  vid.addEventListener("seeked",()=>{if(st)st.newFrame=true;});
  vid.addEventListener("timeupdate",()=>{if(!st)return;if(!st.seeking&&vid.duration)rng.value=Math.round(vid.currentTime/vid.duration*1000);ov.querySelector(".tc").textContent=fd(vid.currentTime)||"0:00";});
  rng.addEventListener("input",()=>{st.seeking=true;if(vid.duration){const t=rng.value/1000*vid.duration;ov.querySelector(".tc").textContent=fd(t)||"0:00";vid.currentTime=t;}});
  rng.addEventListener("change",()=>{st.seeking=false;});
  if(vid.requestVideoFrameCallback){const cb=()=>{if(!st||st.vid!==vid)return;st.newFrame=true;vid.requestVideoFrameCallback(cb);};vid.requestVideoFrameCallback(cb);}
  ov.addEventListener("click",onClick);
  cv.addEventListener("pointerdown",pDown);cv.addEventListener("pointermove",pMove);cv.addEventListener("pointerup",pUp);cv.addEventListener("pointercancel",pUp);
  cv.addEventListener("wheel",e=>{e.preventDefault();zoom(Math.exp(e.deltaY/600));},{passive:false});
  window.addEventListener("resize",onResize);document.addEventListener("keydown",onKey);
  setMode(+lsGet("fs_p360mode","0"));
  vgBackPush(close);
  vid.play().catch(()=>{});
  setTimeout(()=>{const h=ov.querySelector(".p3hint");if(h)h.style.opacity=0;},3500);
  loop();}
function msg(h,soft){const m=st&&st.ov.querySelector(".p3msg");if(!m)return;m.innerHTML=h;m.hidden=false;if(soft)setTimeout(()=>{m.hidden=true;},4000);}
function showCtl(){if(!st)return;st.ov.classList.remove("hide");clearTimeout(st.hideT);}
function hideLater(){if(!st)return;clearTimeout(st.hideT);st.hideT=setTimeout(()=>{if(st&&!st.vid.paused)st.ov.classList.add("hide");},3500);}
function setMode(m){st.mode=m;try{localStorage.setItem("fs_p360mode",String(m));}catch(e){}st.ov.querySelectorAll("[data-m]").forEach(b=>b.classList.toggle("on",+b.dataset.m===m));st.dirty=true;}
function zoom(f){if(st.mode===1)st.pfov=Math.max(90,Math.min(345,st.pfov*f));else if(st.mode===0)st.fov=Math.max(20,Math.min(130,st.fov*f));st.dirty=true;}
function reset(){st.yaw=st.pitch=st.roll=0;st.fov=90;st.pyaw=st.ppitch=0;st.pfov=270;if(st.gyro&&st.gR)st.gyaw=-heading(st.gR);st.dirty=true;}
function heading(R){const f=[-R[2],-R[5],-R[8]];return Math.atan2(-f[0],-f[2]);}
function onGyro(e){if(e.alpha==null)return;const o=((screen.orientation&&screen.orientation.angle)||window.orientation||0)*D2R;
  st.gR=mul(mul(mul(mul(Ry(e.alpha*D2R),Rx(e.beta*D2R)),Rz(-e.gamma*D2R)),Rx(-Math.PI/2)),Rz(-o));
  if(st.gyaw==null)st.gyaw=st.yaw-heading(st.gR);st.dirty=true;}
async function toggleGyro(){const b=st.ov.querySelector('[data-k="gyro"]');
  if(st.gyro){st.gyro=false;window.removeEventListener("deviceorientation",onGyro);b.classList.remove("on");if(st.gR)st.yaw=st.gyaw+heading(st.gR);st.pitch=0;st.dirty=true;return;}
  try{if(typeof DeviceOrientationEvent!=="undefined"&&DeviceOrientationEvent.requestPermission){const r=await DeviceOrientationEvent.requestPermission();if(r!=="granted"){toast("沒有取得陀螺儀權限");return;}}}catch(e){}
  if(!window.isSecureContext){toast("陀螺儀需要 https 網址（用 Tailscale 的 https 網址開）");}
  st.gyro=true;st.gyaw=null;st.gR=null;window.addEventListener("deviceorientation",onGyro);b.classList.add("on");
  setTimeout(()=>{if(st&&st.gyro&&!st.gR){toast("這支手機沒有回報陀螺儀資料");}},2000);}
function onClick(e){const b=e.target.closest("button");
  if(!b){return;}
  e.stopPropagation();showCtl();const v=st.vid,k=b.dataset.k;
  if(b.dataset.m!==undefined){setMode(+b.dataset.m);return;}
  if(k==="x"){close();return;}
  if(k==="play"){v.paused?v.play().catch(()=>{}):v.pause();return;}
  if(k==="b10"){v.currentTime=Math.max(0,v.currentTime-10);return;}
  if(k==="f10"){v.currentTime=Math.min(v.duration||0,v.currentTime+10);return;}
  if(k==="spd"){const i=(st.speeds.indexOf(st.spd)+1)%st.speeds.length;st.spd=st.speeds[i];v.playbackRate=st.spd;b.textContent=st.spd+"×";return;}
  if(k==="mute"){v.muted=!v.muted;b.textContent=v.muted?"🔇":"🔊";return;}
  if(k==="fs"){const el=st.ov;if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});else (el.requestFullscreen?el.requestFullscreen():Promise.reject()).then(()=>{try{screen.orientation.lock("landscape").catch(()=>{});}catch(e){}}).catch(()=>{});return;}
  if(k==="gyro"){toggleGyro();return;}
  if(k==="reset"){reset();return;}
  if(k==="plain"){const s=v.src;close();const p=$("#player");if(p)p.innerHTML=`<div class="panel"><video controls autoplay playsinline src="${s}"></video></div>`;return;}}
function pDown(e){cvCap(e);st.ptrs.set(e.pointerId,{x:e.clientX,y:e.clientY});st.moved=0;
  st.g0={yaw:st.yaw,pitch:st.pitch,roll:st.roll,fov:st.fov,pyaw:st.pyaw,ppitch:st.ppitch,pfov:st.pfov,gyaw:st.gyaw,pts:[...st.ptrs.values()].map(p=>({...p}))};}
function cvCap(e){try{st.cv.setPointerCapture(e.pointerId);}catch(_){}}
function pMove(e){if(!st.ptrs.has(e.pointerId))return;st.ptrs.set(e.pointerId,{x:e.clientX,y:e.clientY});const P=[...st.ptrs.values()],g=st.g0;if(!g)return;
  if(P.length===1&&g.pts.length===1){const dx=P[0].x-g.pts[0].x,dy=P[0].y-g.pts[0].y;st.moved=Math.max(st.moved,Math.abs(dx)+Math.abs(dy));const h=st.cv.clientHeight||1;
    if(st.mode===1){const k=180/h*D2R;st.pyaw=g.pyaw+dx*k;st.ppitch=g.ppitch+dy*k;}
    else if(st.mode===0){const k=st.fov/h*D2R;if(st.gyro)st.gyaw=(g.gyaw||0)+dx*k;else{st.yaw=g.yaw+dx*k;st.pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,g.pitch+dy*k));}}
    st.dirty=true;}
  else if(P.length>=2&&g.pts.length>=2){const d=(a,b)=>Math.hypot(a.x-b.x,a.y-b.y),an=(a,b)=>Math.atan2(b.y-a.y,b.x-a.x);
    const f=d(g.pts[0],g.pts[1])/Math.max(1,d(P[0],P[1]));st.moved=99;
    if(st.mode===1)st.pfov=Math.max(90,Math.min(345,g.pfov*f));else if(st.mode===0){st.fov=Math.max(20,Math.min(130,g.fov*f));st.roll=g.roll-(an(P[0],P[1])-an(g.pts[0],g.pts[1]));}
    st.dirty=true;}}
function pUp(e){const had=st.ptrs.size;st.ptrs.delete(e.pointerId);
  if(had===1&&st.moved<8){const now=Date.now();if(now-st.lastTap<320){reset();st.lastTap=0;}else{st.lastTap=now;setTimeout(()=>{if(st&&st.lastTap===now){st.ov.classList.contains("hide")?(showCtl(),hideLater()):st.ov.classList.add("hide");}},330);}}
  const P=[...st.ptrs.values()];st.g0={yaw:st.yaw,pitch:st.pitch,roll:st.roll,fov:st.fov,pyaw:st.pyaw,ppitch:st.ppitch,pfov:st.pfov,gyaw:st.gyaw,pts:P.map(p=>({...p}))};}
function onKey(e){if(!st)return;const v=st.vid;if(e.key===" "){e.preventDefault();v.paused?v.play():v.pause();}else if(e.key==="ArrowLeft")v.currentTime-=5;else if(e.key==="ArrowRight")v.currentTime+=5;else if(e.key==="Escape")close();}
function onResize(){if(st)st.dirty=true;}
function loop(){if(!st)return;st.raf=requestAnimationFrame(loop);const {gl,cv,vid}=st;if(!gl)return;
  const dpr=Math.min(2,window.devicePixelRatio||1),W=Math.round(cv.clientWidth*dpr),H=Math.round(cv.clientHeight*dpr);
  if(cv.width!==W||cv.height!==H){cv.width=W;cv.height=H;st.dirty=true;}
  const up=vid.readyState>=2&&(st.newFrame||(!vid.requestVideoFrameCallback&&!vid.paused)||st.first!==true);
  if(up){try{gl.texImage2D(gl.TEXTURE_2D,0,gl.RGB,gl.RGB,gl.UNSIGNED_BYTE,vid);st.first=true;}catch(e){}st.newFrame=false;st.dirty=true;}
  if(!st.dirty)return;st.dirty=false;
  gl.viewport(0,0,W,H);let M;
  if(st.mode===1)M=mul(Ry(st.pyaw),Rx(st.ppitch));
  else if(st.gyro&&st.gR)M=mul(mul(Ry(st.gyaw||0),st.gR),Rz(st.roll));
  else M=mul(mul(Ry(st.yaw),Rx(st.pitch)),Rz(st.roll));
  gl.uniformMatrix3fv(st.u.M,false,new Float32Array(colMajor(M)));const asp=W/Math.max(1,H),nar=asp<1?1/asp:1;gl.uniform1f(st.u.tanH,Math.min(6,Math.tan(st.fov*D2R/2)*nar));gl.uniform1f(st.u.asp,asp);
  gl.uniform1f(st.u.pk,2*Math.tan(st.pfov*D2R/4)*nar);gl.uniform1i(st.u.mode,st.mode);gl.drawArrays(gl.TRIANGLES,0,3);}
function close(silent){if(!st)return;const s=st;st=null;cancelAnimationFrame(s.raf);try{s.vid.pause();s.vid.removeAttribute("src");s.vid.load();}catch(e){}
  window.removeEventListener("deviceorientation",onGyro);window.removeEventListener("resize",onResize);document.removeEventListener("keydown",onKey);
  if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});s.ov.remove();if(silent!==true)vgBackDone(close);}
return {open,close};})();

let OUTSEL=null;
async function viewOuts(){$("#clk").textContent="";const host=V();if(OUTSEL===null)OUTSEL=lsGet("fs_outdir","");
  if(!host.querySelector("#oLst"))host.innerHTML='<div class="empty">讀取中…</div>';
  let d;try{d=await J("/api/outputs?dir="+encodeURIComponent(OUTSEL));}catch(e){host.innerHTML=offline;return;}
  if(OUTSEL!==""&&!d.folders.some(f=>String(f.i)===OUTSEL)){OUTSEL="";lsSet("fs_outdir","");return viewOuts();}
  if(OUTSEL==="")lsSet("fs_outs_seen",String(d.items.length?d.items[0].mtime:0));if($("#no"))$("#no").hidden=true;
  const fname=i=>{const f=d.folders.find(x=>x.i===i);return f?f.name:"";};
  const all=d.folders.reduce((a,f)=>a+f.count,0);
  const chips=`<div class="ofc"><button class="ofb${OUTSEL===""?" on":""}" data-od="">全部 <b>${all}</b></button>${d.folders.map(f=>`<span class="ofb${OUTSEL===String(f.i)?" on":""}${f.exists?"":" miss"}"><button data-od="${f.i}" title="${esc(f.path)}">${f.current?"📦":"🗂"} ${esc(f.name)} <b>${f.count}</b>${f.exists?"":" ⚠"}</button>${f.removable?`<button class="ofx" data-orm="${esc(f.path)}" title="從清單移除（不會刪除電腦上的資料夾和檔案）">✕</button>`:""}</span>`).join("")}<button class="ofb ofadd" id="oAdd">＋ 加入資料夾</button><button class="ofb" id="oTag" title="過渡用：V2.19.5 以前的影片補上色彩標記（不重新轉檔）">🎨 補色彩標記</button><button class="ofb" id="oChk" title="🧪 找出壞檔、沒有 360 資訊、沒有色彩標記、長度不對的成品">🧪 健檢</button></div>`;
  const sh=OUTSEL===""?`${d.folders.length} 個資料夾`:esc(d.folder);
  host.innerHTML=`<style>.ofc{display:flex;gap:6px;flex-wrap:wrap;margin:2px 0 6px}.ofb{display:inline-flex;align-items:center;background:var(--btn);border:1px solid var(--line2);border-radius:999px;color:var(--text);font:inherit;font-size:13px;overflow:hidden}
.ofb>button,button.ofb{background:none;border:0;color:inherit;font:inherit;padding:6px 11px}.ofb b{color:var(--sub);font-weight:700;margin-left:3px}.ofb.on{background:#16324a;border-color:#2b8ad8;color:#fff}.ofb.on b{color:#9fd3ff}
.ofx{border-left:1px solid var(--line2)!important;padding:6px 9px!important;color:#ff8a98!important}.ofb.miss{opacity:.6}.ofadd{color:#4dc9ff!important}.ofn{font-size:11.5px;color:#4dc9ff}</style>
<div class="stk">${chips}<div class="snote" style="margin:0 2px">${sh} · ${d.items.length}${d.total>d.items.length?` / ${d.total}`:""} 個 · 點 ▶ 用 360 播放（可用陀螺儀、手指拖曳、雙指縮放）</div><div id="player"></div></div><div id="oLst">`+(d.items.map(o=>`<div class="c"><div class="cb"><img class="th" style="width:128px;height:72px" loading="lazy" src="${B}/api/outthumb?name=${encodeURIComponent(o.name)}">
<div class="ci"><div class="nm" style="white-space:normal;word-break:break-all;font-size:13.5px">${esc(o.file)}</div><div class="r2"><span class="chip sz">${fmtB(o.size)}</span><span>${fmtClk(o.mtime)}</span>${OUTSEL===""&&d.folders.length>1?`<span class="ofn">${d.folders[o.dir]&&d.folders[o.dir].current?"📦":"🗂"} ${esc(fname(o.dir))}</span>`:""}</div></div></div>
<div class="acts">${o.video?`<button class="pill" data-play="${esc(o.name)}">▶ 播放</button>`:""}<a class="pill" style="text-decoration:none;display:flex;align-items:center;justify-content:center" href="${B}/api/file?name=${encodeURIComponent(o.name)}" download>⬇ 下載</a><button class="pill" data-rq="${esc(o.name)}">↻ 重新轉檔</button>${d.folders[o.dir]&&d.folders[o.dir].current?`<button class="pill" data-del="${esc(o.name)}" data-fn="${esc(o.file)}" style="flex:none;color:#ff6577">🗑</button>`:""}</div></div>`).join("")||'<div class="empty">這裡還沒有影片</div>')+`</div>`;
  const play=n=>{const it=d.items.find(o=>o.name===n);if(!it||it.video){P360.open(`${B}/api/file?name=${encodeURIComponent(n)}`,it?it.file:n);return;}const p=$("#player");p.innerHTML=`<div class="panel"><div class="r1"><b class="nm" style="white-space:normal;font-size:13px">${esc(it?it.file:n)}</b><button class="pill" id="pcl" style="flex:none;margin-left:auto">✕</button></div><video controls autoplay playsinline src="${B}/api/file?name=${encodeURIComponent(n)}"></video><div class="snote">360 影片在手機瀏覽器會顯示攤平的畫面（App 內 360 播放器下一版加入）</div></div>`;$("#pcl").onclick=()=>p.innerHTML="";};
  host.onclick=async e=>{const b=e.target.closest("button");if(!b)return;
    if(b.dataset.od!==undefined){OUTSEL=b.dataset.od;lsSet("fs_outdir",OUTSEL);viewOuts();return;}
    if(b.dataset.orm){if(confirm("把這個資料夾從成品清單移除？\n\n只是不在這裡顯示，電腦上的資料夾和裡面的影片都不會刪除。\n\n"+b.dataset.orm)){await act("/api/outfolders/remove",{path:b.dataset.orm});OUTSEL="";lsSet("fs_outdir","");viewOuts();}return;}
    if(b.id==="oChk"){outChk(OUTSEL===""?"":(d.folders.find(f=>String(f.i)===OUTSEL)||{}).path||"");return;}
    if(b.id==="ocGo"){const it=[...document.querySelectorAll("#player [data-oc]:checked")].map(x=>({path:x.dataset.oc,action:x.dataset.act}));if(!it.length)return toast("沒有勾選");const j=await act("/api/outcheck/run",{items:it});if(j.ok)ctWatch();return;}
    if(b.id==="oTag"){colorTag(OUTSEL===""?"":(d.folders.find(f=>String(f.i)===OUTSEL)||{}).path||"");return;}
    if(b.id==="ctGo"){const ps=[...document.querySelectorAll("#player input[data-ct]:checked")].map(x=>x.dataset.ct);if(!ps.length)return toast("沒有勾選");const j=await act("/api/colortag/run",{paths:ps});if(j.ok)ctWatch();return;}
    if(b.id==="ctX"){$("#player").innerHTML="";return;}
    if(b.id==="oAdd"){pickDir("加入成品資料夾（只是加入清單來瀏覽）","",async p=>{const j=await act("/api/outfolders/add",{path:p});if(j&&j.ok!==false)viewOuts();});return;}
    if(b.dataset.play)play(b.dataset.play);
    else if(b.dataset.rq){if(confirm("用同一段素材、同一個預設重新轉檔？"))await act("/api/outputs/requeue",{name:b.dataset.rq});}
    else if(b.dataset.del){if(confirm("刪除這個成品檔案？（原始素材不受影響）\n\n"+b.dataset.fn)){await act("/api/outputs/delete",{name:b.dataset.del});viewOuts();}}};
  if(S.play){const n=S.play;S.play=null;const it=d.items.find(o=>o.name===n||(o.file===n&&d.folders[o.dir]&&d.folders[o.dir].current));if(it)play(it.name);else toast("成品還在搬運，或不在成品資料夾");}}
async function outChk(dir){const p=$("#player");if(!p)return;
  p.innerHTML='<div class="panel"><b>🧪 成品健檢</b><div class="snote" id="ocS">準備中…</div></div>';
  await post("/api/outcheck/scan",{dir});let d;
  for(let i=0;i<2000;i++){await new Promise(r=>setTimeout(r,800));try{d=await J("/api/outcheck");}catch(e){continue;}const s=$("#ocS");if(s)s.textContent=`檢查中 ${d.done}/${d.total}…`;if(!d.scanning&&d.found)break;}
  if(!d||!d.found){p.innerHTML="";return;}const L=d.found;
  p.innerHTML=`<div class="panel"><div class="r1"><b>🧪 成品健檢</b><button class="pill" id="ctX" style="flex:none;margin-left:auto">✕</button></div>`+
   (L.length?`<div class="snote">${d.total} 支裡有 ${L.length} 支有問題。能補的「補」（不重新轉檔）；壞的「移走」＝搬到同資料夾的「_問題檔」（不會刪）。</div>
<div style="max-height:45vh;overflow:auto;margin:6px 0">${L.map(x=>`<label style="display:flex;gap:8px;padding:6px 2px;font-size:13px;word-break:break-all;border-bottom:1px solid var(--line)"><input type="checkbox" checked data-oc="${esc(x.path)}" data-act="${x.fix?"fix":"move"}"><span><b>${esc(x.name)}</b><br><small style="color:#ff9f43">${esc(x.txt)}</small> → ${x.fix?"🔧 補":"📦 移走"}</span></label>`).join("")}</div>
<button class="pill" id="ocGo" style="width:100%">處理勾選的</button><div class="snote" id="ctSt"></div>`:`<div class="snote">${d.total} 支都沒問題 👍</div>`)+`</div>`;}
async function colorTag(dir){const p=$("#player");if(!p)return;
  p.innerHTML='<div class="panel"><b>🎨 補上色彩標記</b><div class="snote">正在找沒有色彩標記的影片…</div></div>';
  await post("/api/colortag/scan",{dir});let d;
  for(let i=0;i<600;i++){await new Promise(r=>setTimeout(r,700));try{d=await J("/api/colortag");}catch(e){continue;}if(!d.scanning&&d.found)break;}
  if(!d||!d.found){p.innerHTML="";return toast("✖ 找不到結果");}
  const L=d.found;const nm=x=>x.split(/[\\/]/).pop();
  p.innerHTML=`<div class="panel"><div class="r1"><b>🎨 補上色彩標記</b><button class="pill" id="ctX" style="flex:none;margin-left:auto">✕</button></div>`+
   (L.length?`<div class="snote">找到 ${L.length} 支沒有色彩標記的影片（預設全勾）。不重新轉檔、畫質不變、360 資訊保留，一支幾秒；正在播放的檔案會略過。</div>
<div style="max-height:40vh;overflow:auto;margin:6px 0">${L.map(x=>`<label style="display:flex;gap:8px;padding:5px 2px;font-size:13px;word-break:break-all"><input type="checkbox" checked data-ct="${esc(x)}">${esc(nm(x))}</label>`).join("")}</div>
<button class="pill" id="ctGo" style="width:100%">補上勾選的 ${L.length} 支</button><div class="snote" id="ctSt"></div>`:`<div class="snote">都已經有色彩標記了，不用補 👍</div>`)+`</div>`;}
async function ctWatch(){for(;;){await new Promise(r=>setTimeout(r,1000));let d;try{d=await J("/api/colortag");}catch(e){continue;}const el=$("#ctSt");if(!el)return;
  if(d.busy){el.textContent=`補標記中 ${d.done}/${d.total}：${d.cur}`;continue;}
  el.innerHTML=`✔ 完成：成功 ${d.ok.length} 支`+(d.fail.length?`，失敗 ${d.fail.length} 支<br>`+d.fail.map(esc).join("<br>"):"（重新上傳 YouTube 顏色就會正確）");const g=$("#ctGo");if(g)g.hidden=true;return;}}
/* ================= 🤝 右上角 Lv 1～10（跟 SortCut、ComfyRemote 一樣的介面） ================= */
let LVV=null;const LVCOL=l=>l>=8?"#2ecc71":l>=5?"#f1c40f":l>=3?"#ff9f43":"#ff6b6b";
function lvSet(v){LVV=v;const el=$("#lvb");if(!el)return;const on=v.on!==false,l=v.lv||10;el.classList.toggle("off",!on);el.style.setProperty("--lc",LVCOL(l));
  el.querySelector("b").textContent=on?"Lv"+l:"OFF";el.querySelectorAll(".lvbars i").forEach((x,i)=>x.classList.toggle("on",on&&i<l));const m=$("#lvm");if(m)lvBody(m.firstElementChild);}
async function lvTick(){try{lvSet(await J("/api/hs"));}catch(e){}}
function lvBody(box){const v=LVV||{};const P=v.peers||[];const W={0:"閒著",1:"背景小工作",2:"在做重工作",3:"使用者在等"};const M={alone:"照常跑",lead:"這邊先跑",yield:"讓別人先",split:"照分配"};
  const mem=v.members;const isM=a=>mem==null||mem.includes(a);const sh=Math.round((v.share||50)/10);
  const rows=(v.apps||[]).map(a=>{const p=a==="FusionStitch"?{name:"FusionStitch（這台）",want:null}:P.find(x=>x.app===a);
    return `<tr><td><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" data-lvm="${esc(a)}" ${isM(a)?"checked":""}>${esc(a)}</label></td><td>${a==="FusionStitch"?`Lv ${v.lv} · ${esc(M[v.mode]||v.mode||"")}`:p?`${p.lv!=null?`<b style="color:${LVCOL(p.lv)}">Lv ${p.lv}</b>${p.cap!=null?` ${Math.round(p.cap)}%`:""} · `:""}V${esc(p.ver||"")} · ${esc(W[p.want||0]||"")}${p.what?"（"+esc(p.what)+"）":""}${p.rules<3?` <span style="color:#ff9f43">規則第 ${p.rules||1} 版，要更新</span>`:""}`:`<span class="lvmu">沒開／沒看到</span>`}</td></tr>`;}).join("");
  const left=(v.boost_until||0)-Date.now()/1000;
  box.innerHTML=`<button class="lvx" data-lvx="1">✕</button><h3>🤝 效能分配</h3><div><span class="lvbig" style="color:${LVCOL(v.lv||10)}">Lv ${v.lv??"–"}</span> <b>／10</b> <span class="lvmu">${v.cap!=null?`這邊最多用 ${Math.round(v.cap)}%（現在 ${Math.round((v.level||1)*100)}%）`:"不限"}</span></div>
<div style="margin:4px 0">${esc(v.why||"")}</div>${(v.stalled||[]).length?`<div style="margin:4px 0;color:#ff9f43">⚠ ${v.stalled.map(x=>esc(x.name)+"："+esc(x.why)).join("、")} → 當作卡住，大家照常全力跑</div>`:""}<div class="lvmu">顯示卡現在 ${v.gpu==null?"—":Math.round(v.gpu)+"%"}</div>
<div class="lvrow"><label><input type="checkbox" data-lvk="on" ${v.on?"checked":""}> 🤝 握手（跟這台電腦上的程式互相分配效能）</label></div>
<b style="font-size:13px">✅ 這台電腦有誰參與（沒在這台跑的不要勾）</b><table>${rows}</table>
<div class="lvrow"><span>⚖ FusionStitch : SortCut</span><input type="range" min="1" max="9" step="1" value="${sh}" data-lvk="share" style="flex:1;min-width:120px"><b data-lvsv>${sh*10} : ${100-sh*10}</b></div>
<div class="lvrow"><label>🧑‍💻 留給你 <select data-lvk="reserve">${[0,5,10,15,20,30,40].map(x=>`<option ${x===Math.round(v.reserve||0)?"selected":""}>${x}</option>`).join("")}</select>%</label>
 <label><select data-lvk="reserve_when"><option value="active" ${v.reserve_when!=="always"?"selected":""}>你在用電腦時才留</option><option value="always" ${v.reserve_when==="always"?"selected":""}>一直留</option></select></label></div>
<div class="lvrow"><label>👑 ${esc((v.boss||[]).join("、")||"—")} 要效能時用 <select data-lvk="boss_cap">${[50,60,70,80,85,90].map(x=>`<option ${x===Math.round(v.boss_cap||80)?"selected":""}>${x}</option>`).join("")}</select>%</label>
 <label>其他降到 <select data-lvk="others_cap">${[1,3,5,10,15,20].map(x=>`<option ${x===Math.round(v.others_cap||5)?"selected":""}>${x}</option>`).join("")}</select>%</label></div>
<div class="lvrow"><label>👑 幾分鐘沒進度算卡住 <select data-lvk="stall_min">${[2,3,4,5,8,10,15].map(x=>`<option ${x===Math.round(v.stall_min||4)?"selected":""}>${x}</option>`).join("")}</select> 分</label></div>
<div class="lvrow"><label>這邊的優先 <select data-lvk="prio">${[...Array(11)].map((_,i)=>`<option value="${i}" ${i===(v.prio??5)?"selected":""}>${i}${i===5?"（一樣）":i>5?"（這邊先）":"（對方先）"}</option>`).join("")}</select></label>
 ${left>0?`<button class="pill" data-lvb="0">取消先跑（剩 ${Math.ceil(left/60)} 分）</button>`:`<button class="pill" data-lvb="1">⏩ 這邊先跑 1 小時</button>`}</div>
<p class="lvmu">有工作就全力跑：FusionStitch 和 SortCut 都在忙 → 照分配（沒人用電腦 50/50、你在用 45/45）；只有一個在忙 → 它用 100%（你在用 ${100-(v.reserve||0)}%）；${esc((v.boss||[]).join("、"))} 真的在跑 → 它 ${Math.round(v.boss_cap||80)}%、其他 ${Math.round(v.others_cap||5)}%；它 ${Math.round(v.stall_min||4)} 分鐘沒進度（顯示卡沒在動）→ 不讓了。設定三個程式共用，哪邊改都一樣。規則第 ${v.rules} 版${v.rules_from==="lib"?"（別的程式提供的新版）":""}。${v.err?`<br><span style="color:#ff6b6b">${esc(v.err)}</span>`:""}</p>`;}
function lvOpen(){let m=$("#lvm");if(m){m.remove();return;}m=document.createElement("div");m.className="lvm";m.id="lvm";m.innerHTML="<div></div>";document.body.appendChild(m);lvBody(m.firstElementChild);
  m.addEventListener("click",async e=>{if(e.target===m||e.target.closest("[data-lvx]")){m.remove();return;}const b=e.target.closest("[data-lvb]");if(b){await act("/api/hs",{boost:+b.dataset.lvb});lvTick();}});
  m.addEventListener("input",e=>{const t=e.target;if(t.dataset.lvk==="share"){const o=m.querySelector("[data-lvsv]");if(o)o.textContent=`${t.value*10} : ${100-t.value*10}`;}});
  m.addEventListener("change",async e=>{const t=e.target;let body=null;
    if(t.dataset.lvm!=null){const all=[...m.querySelectorAll("[data-lvm]")];body={members:all.filter(x=>x.checked).map(x=>x.dataset.lvm)};}
    else if(t.dataset.lvk){const k=t.dataset.lvk;body={};body[k]=t.type==="checkbox"?t.checked:k==="share"?+t.value*10:k==="reserve_when"?t.value:+t.value;}
    if(body){await act("/api/hs",body);lvTick();}});}
document.addEventListener("click",e=>{if(e.target.closest&&e.target.closest("#lvb"))lvOpen();});
lvTick();setInterval(lvTick,3000);
/* ================= 工具 ================= */
const TOOLS=[["轉檔",[["bg","🖥","後台狀態","現在在轉什麼、排隊什麼、中繼 SSD，可以取消"],["queue","☰","轉檔佇列","暫停、繼續、置頂、重試、全部轉完後關機"],["outs","▸","成品","播放、下載、重新轉檔"]]],
 ["顏色",[["cm","🎨","原廠色彩對標","用一支原廠轉好的影片，學 GoPro Fusion Studio 的顏色"]]],
 ["電腦與紀錄",[["perf","📊","電腦效能","CPU、顯示卡、NVENC、硬碟、溫度、容量"],["logx","📋","LOG 與問題","自我檢查、最近的錯誤、自動處理紀錄、匯出 LOG"],["chv","🆕","版本與更新","📦 上傳更新包、⏪ 退回版本、這次更新了什麼（新的標 🆕）"]]],
 ["其他",[["edit","◉","預覽校正","下一階段：手機上預覽拼接、調色、入出點"]]]];
function toolName(k){for(const [,L] of TOOLS)for(const x of L)if(x[0]===k)return x[1]+" "+x[2];return "工具";}
function viewTools(){$("#clk").textContent="";
  V().innerHTML=`<div class="tbgS" id="tbgS">🖥 後台狀態讀取中…</div>`+TOOLS.map(([g,L])=>`<div class="tgh">${g}</div><div class="wsGrid">${L.map(([k,ic,t,sub])=>`<button class="wsCard" data-tl="${k}"><i>${ic}</i><b>${t}</b><small>${sub}</small></button>`).join("")}</div>`).join("");
  V().onclick=e=>{const b=e.target.closest("[data-tl]");if(b){const k=b.dataset.tl;location.hash=k==="queue"||k==="outs"?k:k==="bg"?"bg":"tool/"+k;return;}if(e.target.closest("#tbgS"))location.hash="bg";};
  J("/api/bg").then(d=>{const el=$("#tbgS");if(!el||!d||!d.sections)return;let r=0,w=0;for(const s of d.sections)for(const x of s.items||[])x.state==="run"?r++:w++;
    el.innerHTML=r||w?`🖥 後台：<b>▶ ${r} 個在跑 · ⏳ ${w} 個排隊</b> <span>看詳細 ›</span>`:`🖥 後台：現在沒有背景工作 ✔ <span>看詳細 ›</span>`;}).catch(()=>{});}
function viewTool(k){$("#clk").textContent="";V().onclick=null;
  V().innerHTML=`<div class="tlh"><button class="back" onclick="location.hash='tools'">‹</button><b>${toolName(k)}</b></div><div class="panel tool1"><div class="sbody" id="sbody"></div></div>`;
  const sb=$("#sbody");if(k==="cm")return drawCm(sb);if(k==="perf")drawPerf(sb);else if(k==="logx")drawLogx(sb);else if(k==="chv"){sb.innerHTML='<section class="ssec" id="cuBox"><h3>☁ 雲端更新</h3><div class="snote">讀取中…</div></section><section class="ssec"><h3>📦 上傳更新包</h3><div id="updIn"></div></section><div id="chvBox"></div>';cuMount($("#cuBox"));updInit();chvMount($("#chvBox"));}
  else sb.innerHTML=`<h2 class="stt">◉ 預覽校正</h2><div class="snote">手機版的預覽校正（拼接預覽、調色、防震、入出點）是下一階段的功能。現在可以在素材庫用「✦ 全自動」：自動對齊、防震、判斷方向、白平衡與調色，再轉出。</div>`;}
/* ================= 🎨 原廠色彩對標 ================= */
async function drawCm(sb){let st,lib;try{[st,lib]=await Promise.all([J("/api/cm/status"),J("/api/library")]);}catch(e){sb.innerHTML=offline;return;}
  const clips=lib.clips.filter(c=>c.kind==="video"&&c.complete),L=st.last;
  sb.innerHTML=`<h2 class="stt">🎨 原廠色彩對標</h2><div class="snote" style="margin:0 2px 10px">選一支用原廠 Fusion Studio 轉好的 360 影片（轉檔時不要開防震）和它的原始素材。從兩支各取 30 個畫面、自動對齊方向，算出顏色對照表。之後「轉檔顏色」選「原廠色彩」，顏色、對比、飽和度就會接近原廠。約 1～3 分鐘，在電腦上算。</div>
<section class="ssec"><div class="si"><b>原廠成品（成品資料夾裡的影片）</b><select class="psel" id="cmR">${st.outputs.map(o=>`<option${/VIDEO_|GoPro|_all/i.test(o)?"":""}>${esc(o)}</option>`).join("")}</select><small>例如 VIDEO_6545(1).mp4</small></div>
<div class="si"><b>同一段素材</b><select class="psel" id="cmC">${clips.map(c=>`<option value="${esc(c.id)}">${esc(c.name)} · ${esc(c.time)}</option>`).join("")}</select></div>
<div class="si"><button class="btn" id="cmGo">🎨 計算原廠色彩</button><div class="umsg" id="cmM"></div><div class="ubar"><i id="cmB"></i></div></div></section>
<section class="ssec"><h3>結果</h3><div id="cmRes">${L?cmRes(L):'<div class="snote">還沒有算過</div>'}</div></section>
<section class="ssec"><h3>轉檔顏色</h3><div class="seg" id="cmMode" style="margin:10px 0"><button data-v="auto"${st.mode==="auto"?' class="on"':""}>自動調色</button><button data-v="gopro"${st.mode==="gopro"?' class="on"':""}>原廠色彩</button></div><small class="snote">強度和預設正面在 設定 → 電腦程式 → 顏色與方向</small></section>`;
  const R=$("#cmR"),C=$("#cmC");const pick=()=>{const g=st.guess&&st.guess[R.value];if(g)C.value=g;};
  const pref=st.outputs.find(o=>/^VIDEO_\d{4}/i.test(o));if(pref)R.value=pref;pick();R.onchange=pick;
  $("#cmMode").onclick=async e=>{const b=e.target.closest("button");if(!b)return;await act("/api/settings",{key:"color_mode",value:b.dataset.v});$("#cmMode").querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));};
  const poll=async()=>{if(!sb.isConnected)return;let s;try{s=await J("/api/cm/status");}catch(e){setTimeout(poll,1500);return;}
    if(s.state==="running"){$("#cmM").textContent=s.msg;$("#cmB").style.width=Math.round(s.frac*100)+"%";$("#cmGo").disabled=true;setTimeout(poll,1000);return;}
    $("#cmGo").disabled=false;if(s.state==="error"){$("#cmM").innerHTML=`<span class="ubad">✖ ${esc(s.error)}</span>`;return;}
    if(s.state==="done"){$("#cmM").innerHTML='<span class="uok">✔ 完成</span>';$("#cmB").style.width="100%";$("#cmRes").innerHTML=cmRes(s.result);}};
  $("#cmGo").onclick=async()=>{const j=await act("/api/cm/start",{ref:R.value,clip:C.value});if(j.ok)poll();};
  if(st.state==="running")poll();}
function cmRes(r){if(!r)return "";return `<div class="r2"><span>${esc(r.time)}</span><span>${esc(r.clip)}</span><span>方向差 ${r.shift_deg}°</span><span>對齊 ${r.align}</span></div>
<div class="stg"><div class="sttl"><b>色差（越小越像原廠）</b><span>${r.err_before} → ${r.err_after}</span><small>0～255，留下來沒用來計算的畫面</small></div>
<div class="sttl"><b>飽和度</b><span>${r.ours_raw.sat} → ${r.ours.sat}</span><small>原廠 ${r.official.sat}</small></div>
<div class="sttl"><b>平均顏色 RGB</b><span style="font-size:13px">${r.ours.mean.join(" ")}</span><small>原廠 ${r.official.mean.join(" ")}</small></div></div>
<img src="${B}/api/cm/preview?t=${Date.now()}" style="width:100%;border-radius:8px;margin-top:6px"><div class="snote">上：我們原本（不調色）　中：套用原廠色彩　下：原廠</div>`;}
/* ================= 📊 效能 ================= */
const PFC={cpu:"#4dc9ff",ram:"#b48cff",disk:"#45d483",gpu:"#2ed3b7",enc:"#f0ad4e",dec:"#ff7eb6",fps:"#ffd166"};
function pfSeries(P,key){const H=P.hist||[];
  if(key==="cpu"||key==="ram"||key==="gpu"||key==="enc"||key==="dec")return {v:H.map(h=>h[key]),max:100,c:PFC[key]};
  if(key==="fps"){const v=H.map(h=>h.fps);return {v,max:Math.max(10,...v.filter(x=>x!=null))*1.2,c:PFC.fps};}
  if(key.startsWith("disk:")){const L=key.slice(5),v=H.map(h=>(h.disk||{})[L]);return {v,max:Math.max(50,...v.filter(x=>x!=null))*1.15,c:PFC.disk};}
  return {v:[],max:100,c:"#888"};}
function pfDraw(cv,s,big){if(!cv)return;const r=cv.getBoundingClientRect(),dpr=window.devicePixelRatio||1,W=r.width,H=r.height;if(!W)return;
  cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);const g=cv.getContext("2d");g.setTransform(dpr,0,0,dpr,0,0);g.clearRect(0,0,W,H);
  if(big){g.strokeStyle="rgba(128,128,128,.18)";g.lineWidth=1;for(let i=1;i<10;i++){const x=Math.round(W*i/10)+.5;g.beginPath();g.moveTo(x,0);g.lineTo(x,H);g.stroke();}
    for(let i=1;i<5;i++){const y=Math.round(H*i/5)+.5;g.beginPath();g.moveTo(0,y);g.lineTo(W,y);g.stroke();}}
  const N=180,v=s.v;if(v.length){g.beginPath();let st=false;const x0=W-(v.length-1)/(N-1)*W;
    v.forEach((y,i)=>{if(y==null)return;const X=x0+i/(N-1)*W,Y=H-Math.min(1,y/s.max)*(H-2)-1;st?g.lineTo(X,Y):(g.moveTo(X,Y),st=true);});
    if(st){g.strokeStyle=s.c;g.lineWidth=big?1.6:1.2;g.stroke();g.lineTo(W,H);g.lineTo(x0,H);g.closePath();g.globalAlpha=.18;g.fillStyle=s.c;g.fill();g.globalAlpha=1;}}
  g.strokeStyle=s.c;g.globalAlpha=.55;g.lineWidth=1;g.strokeRect(.5,.5,W-1,H-1);g.globalAlpha=1;}
function pfTiles(P){const T=[],g=(P.gpus||[])[0]||{};
  T.push({k:"cpu",t:"CPU",v:P.cpu!=null?Math.round(P.cpu)+"%":"—",sub:P.cores?P.cores+" 執行緒":""});
  T.push({k:"ram",t:"記憶體",v:P.ram!=null?Math.round(P.ram)+"%":"—",sub:P.ram_total?fmtB(P.ram_used)+" / "+fmtB(P.ram_total):""});
  if(g.use!=null){T.push({k:"gpu",t:"GPU 3D（拼接）",v:Math.round(g.use)+"%",sub:(g.temp!=null?Math.round(g.temp)+"°C · ":"")+esc(g.name||"")});
    T.push({k:"enc",t:"NVENC（編碼）",v:Math.round(g.enc||0)+"%",sub:g.power!=null?"⚡ "+Math.round(g.power)+" W":""});T.push({k:"dec",t:"NVDEC（解碼）",v:Math.round(g.dec||0)+"%",sub:g.mem_total?"顯示記憶體 "+Math.round(g.mem_used/g.mem_total*100)+"%":""});}
  for(const d of P.drives||[])T.push({k:"disk:"+d.d,t:`磁碟 ${d.d}`,v:Math.round(d.busy)+"%",sub:`讀 ${fmtR(d.rd)} · 寫 ${fmtR(d.wr)}`});
  T.push({k:"fps",t:"轉檔速度",v:P.job?P.job.fps.toFixed(1)+" fps":"閒置",sub:P.job?esc(P.job.title)+" "+(P.job.progress*100).toFixed(0)+"%":""});
  return T;}
function pfDetail(P,k){const g=(P.gpus||[])[0]||{},it=[];
  if(k==="cpu")it.push(["使用率",P.cpu!=null?Math.round(P.cpu)+"%":"—"],["執行緒",P.cores||"—"]);
  else if(k==="ram")it.push(["已用",fmtB(P.ram_used)],["總共",fmtB(P.ram_total)]);
  else if(k==="gpu"||k==="enc"||k==="dec")it.push(["顯示卡",esc(g.name||"")],["3D",Math.round(g.use||0)+"%"],["NVENC",Math.round(g.enc||0)+"%"],["NVDEC",Math.round(g.dec||0)+"%"],["溫度",g.temp!=null?Math.round(g.temp)+"°C":"—"],["功耗",g.power!=null?Math.round(g.power)+(g.power_limit?" / "+Math.round(g.power_limit):"")+" W":"—"],["顯示記憶體",g.mem_total?Math.round(g.mem_used)+" / "+Math.round(g.mem_total)+" MB":"—"]);
  else if(k.startsWith("disk:")){const d=(P.drives||[]).find(x=>"disk:"+x.d===k)||{};it.push(["忙碌",Math.round(d.busy||0)+"%"],["讀",fmtR(d.rd)],["寫",fmtR(d.wr)],["剩餘",d.free!=null?fmtB(d.free):"—"],["總共",d.total!=null?fmtB(d.total):"—"]);}
  else if(k==="fps"&&P.job)it.push(["工作",esc(P.job.title)],["速度",P.job.fps.toFixed(1)+" fps"],["進度",(P.job.progress*100).toFixed(1)+"%"],["剩下",fd(P.job.eta)]);
  return it.map(([a,b])=>`<div><span>${a}</span><b>${b}</b></div>`).join("");}
function pfRender(host){const P=S.pf;if(!P||!host||!host.isConnected)return;const tiles=pfTiles(P);if(!S.pfSel||!tiles.find(t=>t.k===S.pfSel))S.pfSel=tiles[0].k;const sel=tiles.find(t=>t.k===S.pfSel);
  const keepY=host.scrollTop,g=(P.gpus||[])[0]||{};
  const temps=[["顯示卡",g.temp]];
  host.innerHTML=`<h2 class="stt">📊 電腦效能 <small style="font-size:12px;color:var(--sub);font-weight:600">${P.time} · 每 2 秒</small></h2>
<section class="ssec pfbig"><div class="pfhd"><b style="color:${pfSeries(P,sel.k).c}">${esc(sel.t)}</b><span>${sel.v}</span></div><canvas id="pfc" class="pfcv"></canvas>
<div class="pfax"><span>3 分鐘</span><span>${sel.k.startsWith("disk:")?"MB/s（讀＋寫）":sel.k==="fps"?"fps":"0–100%"}</span><span>現在</span></div><div class="pfdt">${pfDetail(P,sel.k)}</div></section>
<div class="pft">${tiles.map(t=>`<button class="pftl${t.k===S.pfSel?" on":""}" data-k="${t.k}"><canvas></canvas><div><b>${esc(t.t)}</b><span>${t.v}</span><small>${t.sub||""}</small></div></button>`).join("")}</div>
<section class="ssec"><h3>🌡 溫度</h3><div class="pftemp">${temps.map(([n,t])=>`<div><span>${n}</span><b class="${t==null?"":t>=80?"hot":t>=50?"warm":""}">${t==null?"—":Math.round(t)+"°C"}</b></div>`).join("")}</div></section>
<section class="ssec"><h3>💾 容量</h3><div class="stg">${(P.drives||[]).filter(d=>d.total).map(d=>{const pu=Math.round((1-d.free/d.total)*100);return `<div class="sttl ${pu>=90?"bad":pu>=75?"warn":""}"><b>💽 ${esc(d.d)}</b><span>剩 ${fmtB(d.free)}</span><i><u style="width:${pu}%"></u></i><small>已用 ${pu}% · 共 ${fmtB(d.total)}</small></div>`;}).join("")||'<div class="snote">讀取中…</div>'}</div></section>`;
  host.scrollTop=keepY;pfDraw(host.querySelector("#pfc"),pfSeries(P,sel.k),true);
  host.querySelectorAll(".pftl").forEach(b=>{pfDraw(b.querySelector("canvas"),pfSeries(P,b.dataset.k),false);b.onclick=()=>{S.pfSel=b.dataset.k;pfRender(host);};});}
function drawPerf(host){host.innerHTML='<h2 class="stt">📊 電腦效能</h2><div class="empty">讀取中…</div>';
  const tick=async()=>{if(!host.isConnected){clearInterval(iv);return;}if(document.hidden)return;let j;try{j=await J("/api/perf");}catch(e){return;}if(j.error){host.innerHTML=`<div class="empty">${esc(j.error)}</div>`;return;}S.pf=j;pfRender(host);};
  const iv=setInterval(tick,2000);tick();}
/* ================= 🖥 背景（後台狀態） ================= */
const BGS={root:null,iv:0,d:null};
function bgsMount(root){BGS.root=root;root.classList.add("bgs");root.innerHTML=`<div class="bgsHd"><b>🖥 後台狀態</b><span class="bgsSum" id="bgsSum">讀取中…</span></div><div class="bgsNote">電腦現在在背景做的事。▶ 正在跑、⏳ 排隊中；有「取消」的可以直接取消。</div><div id="bgsL"></div>`;
  root.onclick=async e=>{const b=e.target.closest("[data-bgc]");if(!b)return;if(!confirm(`取消這一項？\n${b.dataset.t}`))return;b.disabled=true;await act("/api/bg/cancel",{key:b.dataset.bgc,id:b.dataset.id});bgsLoad();};bgsLoad();}
async function bgsLoad(){clearTimeout(BGS.iv);if(!BGS.root||!BGS.root.isConnected)return;let d=null;try{d=await J("/api/bg");}catch(e){}
  if(!BGS.root||!BGS.root.isConnected)return;if(d&&d.sections){BGS.d=d;bgsDraw();}BGS.iv=setTimeout(bgsLoad,document.hidden?8000:2500);}
function bgsDraw(){const R=BGS.root;if(!R)return;const SS=BGS.d.sections||[];let run=0,wait=0;
  const h=SS.map(sec=>{const it=sec.items||[];const r=it.filter(x=>x.state==="run").length,w=it.length-r;run+=r;wait+=w;
    return `<div class="bgsC${it.length?"":" idle"}"><div class="bgsCH"><b>${esc(sec.title)}</b><span>${it.length?`${r?`▶ ${r} 個在跑`:""}${r&&w?" · ":""}${w?`⏳ ${w} 個排隊`:""}`:"沒有"}</span></div>${sec.note&&it.length?`<div class="bgsCN">${esc(sec.note)}</div>`:""}
<div class="bgsLst" data-k="${esc(sec.key)}">${it.slice(0,300).map(x=>`<div class="bgsI ${x.state}"><i>${x.state==="run"?"▶":"⏳"}</i><div class="bgsT"><b>${esc(x.text)}</b><small>${esc(x.sub||"")}</small>${x.pct!=null&&x.state==="run"?`<u><s style="width:${Math.max(2,Math.min(100,+x.pct||0))}%"></s></u>`:""}</div>${x.cancel?`<button data-bgc="${esc(sec.key)}" data-id="${esc(x.id)}" data-t="${esc(x.text)}">✕ 取消</button>`:""}</div>`).join("")}</div>${it.length>5?`<div class="bgsMore">共 ${it.length} 個 · 上下捲看其他的</div>`:""}</div>`;}).join("");
  const sv={};R.querySelectorAll(".bgsLst").forEach(x=>{sv[x.dataset.k]=x.scrollTop;});R.querySelector("#bgsL").innerHTML=h;R.querySelectorAll(".bgsLst").forEach(x=>{if(sv[x.dataset.k])x.scrollTop=sv[x.dataset.k];});
  const sm=R.querySelector("#bgsSum");sm.textContent=(BGS.d.guard?"⏸ "+BGS.d.guard+" · ":"")+(run||wait?`▶ ${run} 個在跑 · ⏳ ${wait} 個排隊`:"現在沒有背景工作 ✔");sm.classList.toggle("bgsGuard",!!BGS.d.guard);}
/* ================= 浮窗：📊 效能 · 🖥 背景 · ✂ 轉檔 ================= */
function sysFabInit(){if(document.getElementById("sysFab"))return;const b=document.createElement("div");b.id="sysFab";
  b.innerHTML='<button type="button" data-t="pf" title="電腦效能（浮動視窗，原本的頁面不會停）">📊 效能</button><button type="button" data-t="bg" title="後台狀態（浮動視窗）">🖥 背景</button><button type="button" data-t="ed" title="轉檔：正在轉、排隊、做好的（點一筆播放）">✂ 轉檔<em class="fbN" hidden></em><em class="fbR" hidden></em></button>';
  b.onclick=e=>{const x=e.target.closest("[data-t]");if(!x)return;e.stopPropagation();const w=document.getElementById("sysPop");
    if(!w)sysPopOpen(x.dataset.t);else if(w.dataset.t===x.dataset.t)sysPopClose();else w._tab(x.dataset.t);};document.body.appendChild(b);
  edbPoll();setInterval(()=>{if(!document.hidden)edbPoll();},8000);}
const EDB={d:null};
function edbSeen(){return +lsGet("fs_edb_seen",NaN);}
function edbMark(){const d=EDB.d;if(!d)return;lsSet("fs_edb_seen",String(d.files+d.pend));edbPaint();}
async function edbPoll(){let d=null;try{d=await J("/api/badge");}catch(e){}if(!d||!d.ok)return;EDB.d=d;
  if(isNaN(edbSeen())||edbSeen()>d.files+d.pend)edbMark();const w=document.getElementById("sysPop");if(w&&w.dataset.t==="ed")edbMark();edbPaint();
  const nq=$("#nq");if(nq){nq.hidden=!d.run;nq.textContent=d.run;}}
function edbPaint(){const d=EDB.d,b=document.querySelector('#sysFab [data-t="ed"]');if(!d||!b)return;const n=Math.max(0,d.files+d.pend-(edbSeen()||0)),N=b.querySelector(".fbN"),R=b.querySelector(".fbR");
  N.hidden=!n;N.textContent=n>99?"99+":"+"+n;R.hidden=!d.run;R.textContent=d.run;R.title=`正在轉 ${d.run} 個工作`;}
const SYSPOP_T={pf:"📊 電腦效能",bg:"🖥 後台狀態",ed:"✂ 轉檔"};
function sysPopOpen(t0){if(document.getElementById("sysPop"))return;const w=document.createElement("div");w.id="sysPop";
  w.innerHTML=`<div class="sysPopW"><div class="sysPopH"><b class="sysPopTt"></b><span class="sysPopN">點旁邊回到原本的頁面</span><button type="button" class="sysPopX" title="關閉">✕</button></div><div class="sysPopB"><div class="empty">讀取中…</div></div><div class="sysPopB sysPopG" hidden></div><div class="sysPopB sysPopE" hidden><div class="empty">讀取中…</div></div><div class="sysPopR" title="上下拖：調整視窗高度（點兩下恢復 50%）"><i></i></div></div>`;
  document.body.appendChild(w);const body=w.querySelector(".sysPopB"),bgb=w.querySelector(".sysPopG"),edb=w.querySelector(".sysPopE"),W=w.querySelector(".sysPopW");
  const setH=f=>{f=Math.max(.25,Math.min(.9,+f||.5));W.style.height=(f*100)+"%";return f;};let hf=+lsGet("fs_syspop_h",.5)||.5;setH(hf);
  const gr=w.querySelector(".sysPopR");let dg=null;
  gr.onpointerdown=e=>{gr.setPointerCapture(e.pointerId);dg={y:e.clientY,h:W.getBoundingClientRect().height};gr.classList.add("on");e.preventDefault();e.stopPropagation();};
  gr.onpointermove=e=>{if(!dg)return;hf=setH((dg.h+e.clientY-dg.y)/w.getBoundingClientRect().height);};
  gr.onpointerup=gr.onpointercancel=()=>{if(!dg)return;dg=null;gr.classList.remove("on");lsSet("fs_syspop_h",hf.toFixed(3));if(w.dataset.t==="pf")pfRender(body);};
  gr.ondblclick=()=>{hf=setH(.5);try{localStorage.removeItem("fs_syspop_h");}catch(e){}};
  const tab=t=>{w.dataset.t=t;w.querySelector(".sysPopTt").textContent=SYSPOP_T[t]||"";document.querySelectorAll("#sysFab [data-t]").forEach(b=>b.classList.toggle("on",b.dataset.t===t));body.hidden=t!=="pf";bgb.hidden=t!=="bg";edb.hidden=t!=="ed";
    if(t==="pf"&&S.pf)pfRender(body);if(t==="bg"&&BGS.root!==bgb)bgsMount(bgb);if(t==="ed"){edjLoad(edb);edbMark();}};
  w._tab=tab;tab(SYSPOP_T[t0]?t0:"pf");
  w.addEventListener("click",e=>{if(e.target===w)sysPopClose();});w.querySelector(".sysPopX").onclick=()=>sysPopClose();
  const tick=async()=>{if(!body.isConnected){clearInterval(S.pfFx);S.pfFx=null;return;}if(document.hidden)return;
    if(!edb.hidden){edjLoad(edb);return;}if(body.hidden)return;let j;try{j=await J("/api/perf");}catch(e){if(!S.pf)body.innerHTML='<div class="empty">連不到電腦或資料讀取失敗</div>';return;}
    if(j&&j.error){if(!S.pf)body.innerHTML=`<div class="empty">${esc(j.error)}</div>`;return;}S.pf=j;pfRender(body);};
  clearInterval(S.pfFx);S.pfFx=setInterval(tick,2000);tick();vgBackPush(sysPopClose);}
async function edjLoad(host){if(host._busy)return;host._busy=true;let d=null;try{d=await J("/api/queue");}catch(e){}host._busy=false;if(!host.isConnected||!d)return;
  const it=j=>{const p=(j.status==="done"?1:j.progress)*100,cls=j.status==="running"?"run":j.status==="done"?"done":j.status==="error"?"err":"wait";
    return `<div class="edjI ${cls}" data-n="${esc(j.status==="done"?j.name:"")}"><div class="edjT"><b>#${j.id} ${esc(j.title)}</b><em>${(ST[j.status]||[j.status])[0]}</em>${j.status==="running"?`<span>${j.fps.toFixed(1)} fps · 剩 ${fd(j.eta)}</span>`:""}${j.status==="done"?'<span class="edjGo">▶ 播放</span>':""}</div><small>${esc(j.name)}${j.message?" · "+esc(j.message):""}</small>${j.status==="running"?`<i class="edjP"><u style="width:${p.toFixed(1)}%"></u></i>`:""}</div>`;};
  const run=d.jobs.filter(j=>["running","queued","paused","error"].includes(j.status)),done=d.jobs.filter(j=>j.status==="done").slice(-20).reverse();
  let h=run.length?`<div class="edjH">正在轉／排隊 ${run.length}</div>`+run.map(it).join(""):"";
  h+=`<div class="edjH">做好的（最近 ${done.length} 筆）· 點一筆播放</div>`+(done.length?done.map(it).join(""):'<div class="empty">還沒有轉好的</div>');
  const st=host.scrollTop;host.innerHTML=h;host.scrollTop=st;
  host.onclick=e=>{const x=e.target.closest(".edjI[data-n]");if(!x||!x.dataset.n)return;vgBackDrop(sysPopClose);sysPopClose(true);S.play=x.dataset.n;if(location.hash==="#outs")viewOuts();else location.hash="outs";};}
function sysPopClose(fromBack){const w=document.getElementById("sysPop");if(!w)return;w.remove();clearInterval(S.pfFx);S.pfFx=null;
  try{const r=document.getElementById("bgsRoot");if(r&&BGS.root!==r)bgsMount(r);}catch(e){}
  document.querySelectorAll("#sysFab [data-t]").forEach(b=>b.classList.remove("on"));if(fromBack!==true)vgBackDone(sysPopClose);}
/* ================= 📋 LOG 與問題 ================= */
function issuesHtml(I){if(!I)return '<div style="opacity:.7">讀取中…</div>';const col={ok:"#45d483",warn:"#f0ad4e",bad:"#ff4d6d"};
  const ck=(I.checks||[]).map(([lv,t,m])=>`<div style="margin:3px 0"><b style="color:${col[lv]||"inherit"}">${lv==="ok"?"✔":lv==="warn"?"⚠":"✖"} ${esc(t)}</b> <span style="opacity:.8">${esc(m)}</span></div>`).join("");
  const er=(I.errors||[]).length?I.errors.map(e=>`<div style="margin:4px 0"><b style="color:#ff4d6d">⚠ ${esc(e.title)}</b> <span style="opacity:.65;font-size:.9em">${fmtClk(e.t)}</span>${e.detail?`<div class="log" style="max-height:120px;margin-top:3px">${esc(e.detail)}</div>`:""}</div>`).join(""):'<span style="color:#45d483">✔ 最近 7 天沒有失敗的工作</span>';
  const ac=(I.actions||[]).map(a=>`<div>${fmtClk(a.t)} ${a.auto?"🤖":"👆"} ${esc(a.msg)}</div>`).join("")||'<div style="opacity:.7">還沒有自動處理過</div>';
  return `<section class="ssec isu"><h3>🩺 自我檢查</h3><div style="font-size:13px;line-height:1.6">${ck}</div></section>
<section class="ssec isu"><h3>⚠ 最近的錯誤（7 天）</h3><div style="font-size:13px;line-height:1.5">${er}</div></section>
<section class="ssec isu"><h3>🛠 處理紀錄（🤖 自動　👆 手動）</h3><div style="font-size:12.5px;line-height:1.6;max-height:40vh;overflow:auto">${ac}</div></section>`;}
async function issuesRefresh(){try{const j=await J("/api/issues");S.isu=j;const el=$("#isu");if(el){const h=issuesHtml(j);if(el._h!==h){el._h=h;el.innerHTML=h;}}}catch(e){}}
function drawLogx(sb){const loc=t=>{const d=new Date(t);return `${d.getFullYear()}-${p2(d.getMonth()+1)}-${p2(d.getDate())}T${p2(d.getHours())}:${p2(d.getMinutes())}`;};
  sb.innerHTML=`<h2 class="stt">📋 LOG 與問題</h2><section class="ssec"><h3>🩺 卡住診斷</h3><div class="snote" style="margin:0 0 8px">轉檔不動時按這裡：列出每個工作和背景程序現在在做什麼。截圖給我就能找原因（也會寫進 job_log）。</div><button class="btn" id="dgGo">🩺 產生診斷</button><pre id="dgOut" style="white-space:pre-wrap;word-break:break-all;font-size:11.5px;max-height:60vh;overflow:auto;margin-top:8px" hidden></pre></section><div id="isu">${issuesHtml(S.isu)}</div><section class="ssec"><h3>📤 匯出 LOG：要哪一段時間？</h3><div class="snote" style="margin:0 0 8px">把這段時間的轉檔紀錄、自動處理、錯誤、更新紀錄和設定（不含 PIN）打包成一個 zip。產生後按「下載」存到手機，就能用任何 App 傳出去。</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">${[[30,"最近 30 分鐘"],[60,"最近 1 小時"],[180,"最近 3 小時"],[720,"最近 12 小時"]].map(([m,t])=>`<button class="btn s" data-m="${m}" style="width:100%;white-space:nowrap;margin:0">${t}</button>`).join("")}</div>
<div class="strow"><span>從</span><input type="datetime-local" id="lxF" value="${loc(Date.now()-1800e3)}"><span>到</span><input type="datetime-local" id="lxT" value="${loc(Date.now())}"></div>
<button class="btn" id="lxGo">📦 匯出自訂這段時間</button><div id="lxR" style="margin-top:10px"></div></section>
<section class="ssec"><h3>之前匯出的</h3><div id="lxL" class="snote">讀取中…</div></section>
<section class="ssec"><h3>📄 最近的轉檔紀錄</h3><div class="tools" style="margin:6px 0"><select class="pill" id="lgN"><option>job_log.txt</option><option>auto_log.txt</option><option>update_log.txt</option><option>crash_log.txt</option></select><button class="pill" id="lgR" style="flex:none">⟳</button></div><div class="log" id="lgB">讀取中…</div></section>
<p class="snote">zip 裡的「00_摘要.txt」先看就好。</p>`;
  const dl=n=>`${B}/api/logx/dl?f=${encodeURIComponent(n)}`,kb=b=>(b/1024).toFixed(0)+" KB";
  const list=async()=>{try{const j=await J("/api/logx/list");$("#lxL").innerHTML=(j.items||[]).length?j.items.map(x=>`<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--line)"><span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.name)}<br><small>${kb(x.bytes)} · ${fmtClk(x.t)}</small></span><a class="pill" style="flex:none;display:flex;align-items:center;text-decoration:none" href="${dl(x.name)}" download="${esc(x.name)}">⬇ 下載</a></div>`).join(""):"還沒有匯出過";}catch(e){$("#lxL").textContent="讀取失敗";}};
  const make=async qs=>{const R=$("#lxR");R.innerHTML=`<div class="snote">⏳ 電腦正在整理紀錄…（大概幾秒）</div>`;
    try{const j=await J("/api/logx/make?"+qs);if(j.error)throw new Error(j.error);
      R.innerHTML=`<div class="uok">✔ ${esc(j.from)} ～ ${esc(j.to)}，${(j.lines||0).toLocaleString()} 行，${kb(j.bytes)}</div><a class="btn" style="display:block;text-align:center;margin-top:8px" href="${dl(j.name)}" download="${esc(j.name)}">⬇ 下載 ${esc(j.name)}</a><pre style="white-space:pre-wrap;font-size:11px;max-height:50vh;overflow:auto;background:var(--btn);border-radius:10px;padding:8px;margin-top:8px">${esc(j.summary||"")}</pre>`;list();}
    catch(e){R.innerHTML=`<div class="ubad">✖ 匯出失敗：${esc(e.message||e)}</div>`;}};
  const lg=async()=>{try{const j=await J("/api/log?name="+encodeURIComponent($("#lgN").value));const b=$("#lgB");b.textContent=j.text;b.scrollTop=1e9;}catch(e){}};
  sb.querySelectorAll("[data-m]").forEach(b=>b.onclick=()=>make("min="+b.dataset.m));
  $("#lxGo").onclick=()=>{const f=new Date($("#lxF").value).getTime()/1000,t=new Date($("#lxT").value).getTime()/1000;if(!f||!t||t<=f){toast("時間不對");return;}make(`from=${f}&to=${t}`);};
  $("#lgN").onchange=lg;$("#lgR").onclick=lg;list();lg();
  issuesRefresh();clearInterval(S.isuIv);S.isuIv=setInterval(()=>{if(!$("#isu")){clearInterval(S.isuIv);S.isuIv=null;return;}if(!document.hidden)issuesRefresh();},10000);}
/* ================= 🆕 版本與更新（TikLive V0.7.42.042） ================= */
const CHV={root:null,d:null,q:"",op:{},busy:false};
const chvT=v=>String(v||"").split(/\D+/).filter(x=>x!=="").map(Number);
function chvCmp(a,b){const A=chvT(a),C=chvT(b);for(let i=0;i<Math.max(A.length,C.length);i++){const x=A[i]||0,y=C[i]||0;if(x!==y)return x>y?1:-1;}return 0;}
function chvMount(root){CHV.root=root;root.classList.add("chv");root.innerHTML='<div class="chvE">讀取中…</div>';
  root.oninput=e=>{if(e.target.classList.contains("chvQ")){if(e.isComposing)return;CHV.q=e.target.value;chvList();}};
  root.addEventListener("compositionend",e=>{if(e.target.classList.contains("chvQ")){CHV.q=e.target.value;chvList();}});
  root.onclick=e=>{const h=e.target.closest("[data-cv]");if(h){const v=h.dataset.cv;CHV.op[v]=!chvIsOpen(v);chvList();return;}
    const a=e.target.closest("[data-ca]");if(a){const k=a.dataset.ca;if(k==="exp"||k==="col"){(CHV.d.items||[]).forEach(x=>CHV.op[x.version]=k==="exp");chvList();}else if(k==="seen")chvSeen();}};
  J("/api/changelog2").then(j=>{CHV.d=j;CHV.op={};chvDraw();}).catch(()=>{root.innerHTML='<div class="chvE">讀取失敗</div>';});}
function chvNew(v){const D=CHV.d||{};return !!D.seen&&chvCmp(v,D.seen)>0;}
function chvIsOpen(v){if(v in CHV.op)return CHV.op[v];if(CHV.q.trim())return true;const R=CHV.d.items||[];return chvNew(v)||(!R.some(e=>chvNew(e.version))&&R[0]&&R[0].version===v);}
function chvDraw(){const R=CHV.root,D=CHV.d;if(!R||!D)return;const L=D.items||[],nv=L.filter(e=>chvNew(e.version)),ni=nv.reduce((a,e)=>a+(e.items||[]).length,0);
  R.innerHTML=`<div class="chvH"><b>🆕 版本與更新</b><span>目前 V${esc(D.current)}${D.seen?" · 上次看到 V"+esc(D.seen):""}</span></div>
${nv.length?`<div class="chvNw">✨ 上次看過之後有 <b>${nv.length}</b> 個新版本、<b>${ni}</b> 項新功能（標 🆕 的）<button type="button" class="pill" data-ca="seen">✓ 我看完了</button></div>`:`<div class="chvOk">都看過了 ✓</div>`}
<div class="chvBar"><input class="chvQ" type="search" placeholder="🔎 搜尋功能，例如：中繼、聲音、手機" value="${esc(CHV.q)}"><button type="button" class="pill" data-ca="exp">全展開</button><button type="button" class="pill" data-ca="col">全收合</button></div>
<div class="chvL"></div><small class="chvF">只讀程式附帶的版本說明（CHANGELOG.json），不會連網。</small>`;chvList();}
function chvList(){const R=CHV.root;if(!R)return;const box=R.querySelector(".chvL");if(!box)return;const W=CHV.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hit=t=>W.every(w=>t.toLowerCase().includes(w)),mk=t=>{let s=esc(t);W.forEach(w=>{const ew=esc(w).replace(/[.*+?^${}()|[\]\\]/g,"\\$&");s=s.replace(new RegExp(ew,"gi"),m=>"<mark>"+m+"</mark>");});return s;};
  const H=(CHV.d.items||[]).map(e=>{const it=(e.items||[]).map(String),dt=(e.details||[]).map(String),sh=W.length?it.filter(hit):it,shd=W.length?dt.filter(hit):dt;if(W.length&&!sh.length&&!shd.length)return "";const v=String(e.version),o=chvIsOpen(v);
    return `<div class="chvV${chvNew(v)?" nw":""}"><button type="button" class="chvVh" data-cv="${esc(v)}"><i>${o?"▾":"▸"}</i><b>V${esc(v)}</b>${e.title?`<span style="margin-left:0;opacity:.85">${esc(e.title)}</span>`:""}${chvNew(v)?"<em>🆕</em>":""}${v===String(CHV.d.current)?"<u>目前版本</u>":""}<span>${esc(e.date||"")} · ${W.length?(sh.length+shd.length)+" 項符合":it.length+" 項"}</span></button>${o?`<div class="chvI">${sh.map(x=>`<div>•  ${mk(x)}</div>`).join("")}${shd.length?`<details${W.length?" open":""}><summary class="chvD">技術細節與實測</summary>${shd.map(x=>`<div class="chvD">－ ${mk(x)}</div>`).join("")}</details>`:""}</div>`:""}</div>`;}).join("");
  box.innerHTML=H||`<div class="chvE">${W.length?"沒有找到「"+esc(CHV.q)+"」":"沒有版本說明"}</div>`;}
async function chvSeen(){if(CHV.busy)return;CHV.busy=true;const j=await post("/api/changelog/seen",{});CHV.busy=false;if(j&&j.ok){CHV.d.seen=CHV.d.current;CHV.op={};chvDraw();}}
/* ================= 設定（左邊分類＋更新包） ================= */
const SW_ACC=["#e51e63","#ff5c8a","#9b6dff","#4dc9ff","#45d483","#f0ad4e","#ff7043"];
const LSET=[{cat:"look",name:"外觀",icon:"🎨",secs:[{t:"主題與顏色",items:[
  {k:"theme",t:"主題",type:"seg",opts:[["dark","深色"],["light","淺色"],["system","跟手機"]]},
  {k:"accent",t:"強調色",type:"color",sw:SW_ACC,def:"#e51e63",h:"按鈕、選取中的分頁、進度條的顏色"},
  {k:"anim",t:"動畫效果",type:"seg",opts:[[1,"開"],[0,"關"]],num:1}]},
 {t:"大小與顯示",items:[{k:"zoom",t:"整體大小",type:"seg",opts:[[78,"特小"],[90,"小"],[100,"標準"],[115,"大"],[130,"特大"]],num:1},
  {k:"showClk",t:"右上角狀態文字",type:"seg",opts:[[1,"顯示"],[0,"隱藏"]],num:1},{k:"showVer",t:"右上角版本號",type:"seg",opts:[[1,"顯示"],[0,"隱藏"]],num:1}]}]}];
function setItemHtml(it){const cur=UI[it.k],h=it.h?`<small>${it.h}</small>`:"";
  if(it.type==="seg")return `<div class="si"><b>${it.t}</b><div class="seg sset" data-k="${it.k}">${it.opts.map(([v,t])=>`<button data-v="${v}"${String(cur)===String(v)?' class="on"':""}>${t}</button>`).join("")}</div>${h}</div>`;
  if(it.type==="color")return `<div class="si"><b>${it.t}</b><div class="swc" data-k="${it.k}">${it.sw.map(c=>`<button class="sw${(cur||it.def)===c?" on":""}" data-c="${c}" style="background:${c}"></button>`).join("")}<input type="color" data-k="${it.k}" value="${cur||it.def}"><button class="pill" data-rst="${it.k}" style="flex:none">預設</button></div>${h}</div>`;return "";}
function findItem(k){for(const c of LSET)for(const s of c.secs)for(const it of s.items)if(it.k===k)return it;return null;}
function viewSet(){$("#clk").textContent="";const SP=S.stab||"set";
  V().innerHTML=`<div class="stabs" id="stabs">${[["set","設定"],["upd","更新包"]].map(([k,t])=>`<button data-p="${k}"${SP===k?' class="on"':""}>${t}</button>`).join("")}</div>
<div class="spane" data-p="upd"${SP==="upd"?"":" hidden"}><div class="panel setp"><div class="si"><b>關於</b><div class="abt"><span>程式版本</span><b id="abV">V</b><span>這台電腦</span><b>FusionStitch · GoPro Fusion 360</b></div></div>
<div class="si" style="border-bottom:0"><b>程式更新（上傳更新包）</b><div id="updIn"></div></div></div></div>
<div class="spane" data-p="set"${SP==="set"?"":" hidden"}><div class="s2"><aside class="scat" id="scat"></aside><div class="sbody" id="sbody"></div></div></div>`;
  $("#abV").textContent=$("#ver").textContent;updInit();
  $("#stabs").onclick=e=>{const b=e.target.closest("button");if(!b)return;S.stab=b.dataset.p;document.querySelectorAll("#stabs button").forEach(x=>x.classList.toggle("on",x===b));
    document.querySelectorAll(".spane").forEach(x=>x.hidden=x.dataset.p!==S.stab);window.scrollTo(0,0);if(S.stab==="set")drawSetCat();};
  if(SP==="set")drawSetCat();}
function setSidebar(sc){const pc=(S.pc&&S.pc.groups)||[];const btn=(k,ic,t,d)=>`<button data-c="${k}"${S.scat===k?' class="on"':""}><span>${ic} ${t}</span>${d?`<small>${esc(d)}</small>`:""}</button>`;
  sc.innerHTML=`<input class="ssearch" id="ssq" placeholder="🔍 搜尋設定" value="${esc(S.setQ||"")}"><div class="sch">電腦</div>`+btn("perf","📊","電腦效能","CPU、顯示卡、NVENC、硬碟")+btn("logx","📋","LOG 與問題","自我檢查、錯誤、匯出 LOG")+
   `<div class="sch">這支手機（只影響這個瀏覽器）</div>`+LSET.map(c=>btn(c.cat,c.icon,c.name,"主題、顏色、大小")).join("")+
   `<div class="sch">電腦程式</div>`+(S.pc?pc.map((g,i)=>btn("pc:"+i,"💻",g.group,g.items.slice(0,3).map(x=>x.label.replace(/（.*/,"")).join("、"))).join(""):`<button data-c="pc:">💻 讀取中…</button>`);
  const q=sc.querySelector("#ssq");q.oninput=()=>{S.setQ=q.value;clearTimeout(S.setQT);S.setQT=setTimeout(()=>{if(S.setQ.trim()){S.scat="search";drawSetCat();}else if(S.scat==="search"){S.scat="look";drawSetCat();}},250);};
  sc.onclick=e=>{const b=e.target.closest("button[data-c]");if(!b||b.dataset.c==="pc:")return;S.scat=b.dataset.c;S.setQ="";drawSetCat();window.scrollTo(0,0);};}
async function loadPc(){try{S.pc=await J("/api/settings");}catch(e){}}
function pcItemHtml(it,gi,ii){const id=`p${gi}_${ii}`,v=it.value,h=it.hint?`<small>${esc(it.hint)}</small>`:"";let c="";
  if(it.type==="bool")c=`<div class="seg" data-pk="${id}"><button data-v="1"${v?' class="on"':""}>開</button><button data-v="0"${!v?' class="on"':""}>關</button></div>`;
  else if(it.type==="select")c=it.options.length<=4?`<div class="seg" data-pk="${id}">${it.options.map(o=>`<button data-v="${esc(o[0])}"${String(o[0])===String(v)?' class="on"':""}>${esc(o[1])}</button>`).join("")}</div>`:`<select class="psel" data-pk="${id}">${it.options.map(o=>`<option value="${esc(o[0])}"${String(o[0])===String(v)?" selected":""}>${esc(o[1])}</option>`).join("")}</select>`;
  else if(it.type==="multi")c=`<div class="seg" data-pk="${id}" data-multi="1" style="flex-wrap:wrap">${it.options.map(o=>`<button data-v="${esc(o[0])}"${(v||[]).includes(o[0])?' class="on"':""}>${esc(o[1])}</button>`).join("")}</div>`;
  else if(it.type==="dir")c=`<div class="ptxt">${esc(v||"（未設定）")}</div><div class="tools" style="margin-top:6px"><button class="pill" data-pk="${id}" data-dir="1" style="flex:none">📁 選擇…</button>${v?`<button class="pill" data-pk="${id}" data-clr="1" style="flex:none">清除</button>`:""}</div>`;
  else if(it.type==="int"||it.type==="float")c=`<div class="pnum"><button data-pk="${id}" data-st="-1">−</button><input data-pk="${id}" inputmode="decimal" value="${esc(v??"")}"><button data-pk="${id}" data-st="1">＋</button></div>`;
  else c=`<input class="pinp" data-pk="${id}" type="${it.type==="time"?"time":"text"}" value="${esc(v??"")}">`;
  return `<div class="si" id="si_${id}"><b>${esc(it.label)}</b>${c}${h}</div>`;}
function drawPcCat(gi){const g=S.pc.groups[gi];if(!g)return;const sb=$("#sbody");
  sb.innerHTML=`<h2 class="stt">💻 ${esc(g.group)}</h2><section class="ssec">${g.items.map((it,ii)=>pcItemHtml(it,gi,ii)).join("")}</section><p class="snote">「電腦程式」的設定會立刻存到電腦；電腦程式畫面上的設定頁重新開啟後會顯示新的值。</p>`;bindPc(sb);}
function bindPc(sb){const find=id=>{const [gi,ii]=id.slice(1).split("_").map(Number);return S.pc.groups[gi].items[ii];};
  const save=async(it,v,el)=>{const j=await act("/api/settings",{key:it.key,value:v});if(j.ok!==false){it.value=v;const row=el&&el.closest(".si");if(row){row.classList.add("chg");setTimeout(()=>row.classList.remove("chg"),1200);}}};
  sb.onclick=e=>{const b=e.target.closest("button[data-pk]");if(!b)return;const it=find(b.dataset.pk);
    if(b.dataset.dir){pickDir(it.label,it.value,async p=>{await save(it,p,b);drawSetCat();});return;}
    if(b.dataset.clr){save(it,"",b).then(drawSetCat);return;}
    if(b.dataset.st){const inp=sb.querySelector(`input[data-pk="${b.dataset.pk}"]`);const step=it.type==="float"?(it.max<=1?.05:1):1;let v=(+inp.value||0)+step*(+b.dataset.st);v=Math.max(it.min??-1e9,Math.min(it.max??1e9,v));v=it.type==="float"?Math.round(v*100)/100:Math.round(v);inp.value=v;save(it,v,b);return;}
    const seg=b.closest(".seg[data-pk]");if(seg){if(seg.dataset.multi){b.classList.toggle("on");save(it,[...seg.querySelectorAll("button.on")].map(x=>x.dataset.v),b);return;}
      seg.querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));save(it,it.type==="bool"?b.dataset.v==="1":b.dataset.v,b);}};
  sb.querySelectorAll("select[data-pk],input[data-pk]").forEach(el=>el.onchange=()=>{const it=find(el.dataset.pk);save(it,el.value,el);});}
function drawSetSearch(){const q=(S.setQ||"").trim().toLowerCase(),R=[];
  for(const c of LSET)for(const s of c.secs)for(const it of s.items)if((it.t+(it.h||"")).toLowerCase().includes(q))R.push([c.cat,"🎨 "+c.name,it.t,it.k]);
  ((S.pc&&S.pc.groups)||[]).forEach((g,gi)=>g.items.forEach((it,ii)=>{if((it.label+(it.hint||"")+g.group).toLowerCase().includes(q))R.push(["pc:"+gi,"💻 "+g.group,it.label,`p${gi}_${ii}`]);}));
  $("#sbody").innerHTML=`<h2 class="stt">🔍 搜尋「${esc(S.setQ)}」</h2>`+(R.map(([c,g,t,k])=>`<button class="sres" data-go="${c}" data-k="${k}"><small>${esc(g)}</small><b>${esc(t)}</b></button>`).join("")||'<div class="empty">沒有找到</div>');
  $("#sbody").onclick=e=>{const b=e.target.closest("[data-go]");if(!b)return;S.scat=b.dataset.go;S.setQ="";drawSetCat();const el=document.getElementById("si_"+b.dataset.k)||[...document.querySelectorAll(".si")].find(x=>x.querySelector(`[data-k="${b.dataset.k}"]`));if(el){el.scrollIntoView({block:"center"});el.classList.add("sflash");setTimeout(()=>el.classList.remove("sflash"),1800);}};}
function drawSetCat(){if(!S.scat)S.scat="look";const sc=$("#scat");if(sc)setSidebar(sc);
  if(!S.pc&&!S.pcLoading){S.pcLoading=true;loadPc().then(()=>{S.pcLoading=false;if($("#scat"))drawSetCat();});}
  if(S.scat==="search")return drawSetSearch();if(S.scat==="perf")return drawPerf($("#sbody"));if(S.scat==="logx")return drawLogx($("#sbody"));
  if(S.scat.startsWith("pc:")){if(S.pc)drawPcCat(+S.scat.slice(3));else $("#sbody").innerHTML='<div class="empty">讀取中…</div>';return;}
  const c=LSET.find(x=>x.cat===S.scat)||LSET[0];const sb=$("#sbody");
  sb.innerHTML=`<h2 class="stt">${c.icon} ${c.name}</h2>`+c.secs.map(s=>`<section class="ssec"><h3>${s.t}</h3>${s.items.map(setItemHtml).join("")}</section>`).join("")+`<button class="btn s" id="setReset">這支手機的設定全部恢復預設</button><p class="snote">「這支手機」的設定只存在這個瀏覽器，不影響電腦上的 FusionStitch；「電腦程式」分類才會改到電腦。</p>`;
  sb.onclick=e=>{const b=e.target.closest("button");if(!b)return;if(b.id==="setReset"){UI=Object.assign({},UI_DEF);saveUI();applyUI();drawSetCat();toast("已恢復預設");return;}
    const sg=b.closest(".sset");if(sg){const it=findItem(sg.dataset.k);UI[it.k]=it.num?+b.dataset.v:b.dataset.v;saveUI();applyUI();sg.querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));return;}
    const sw=b.closest(".swc");if(sw){const it=findItem(sw.dataset.k);if(b.dataset.rst)UI[it.k]="";else if(b.dataset.c)UI[it.k]=b.dataset.c===it.def?"":b.dataset.c;else return;saveUI();applyUI();drawSetCat();}};
  sb.querySelectorAll('input[type=color][data-k]').forEach(i=>i.oninput=e=>{UI[i.dataset.k]=e.target.value;saveUI();applyUI();});}
/* ---------------- 更新包（TikLive V0.5.52 流程） ---------------- */
async function updInit(){const box=$("#updIn");if(!box)return;
  box.innerHTML=`<div class="snote" style="margin:0 0 8px">選手機裡的 FS_V版本.zip，上傳後會先顯示更新內容，按「確定更新」才會開始。更新時轉檔不會中斷。</div>
<label class="btn ufile">📦 選擇更新包（.zip）<input type="file" id="updF" accept=".zip,application/zip,application/x-zip-compressed,application/octet-stream"></label><div id="updR"></div>
<div class="si" style="border-bottom:0;margin-top:14px;padding-bottom:0"><b>⏪ 退回前面的版本</b><div class="snote" style="margin:0 0 8px">更新後如果出問題，可以退回更新前的版本（用每次更新前自動做的備份，一樣不中斷轉檔）。</div><div id="rbList">讀取中…</div></div>`;
  $("#updF").onchange=e=>{const f=e.target.files&&e.target.files[0];e.target.value="";if(f)updUpload(f);};rbLoad();}
async function rbLoad(){const L=$("#rbList");if(!L)return;let j;try{j=await J("/api/update/rollback");}catch(e){L.textContent="讀不到備份清單";return;}if(!$("#rbList"))return;
  if(!j.items.length){L.innerHTML='<div class="snote" style="margin:0">還沒有可以退回的版本（更新過一次之後才會有）。</div>';return;}
  L.innerHTML=j.items.map((x,i)=>`<div class="upk"><div class="uh"><b style="color:var(--strong);font-size:16px">V${esc(x.version)}</b><span class="ut">${x.older?"前一版":"較新"}</span><small>${esc(x.time)} 更新前備份的</small></div>
${x.notes.length?`<details><summary class="snote">這版的更新內容</summary><ul>${x.notes.map(n=>`<li>${esc(n)}</li>`).join("")}</ul></details>`:""}<button class="btn s" data-i="${i}">退回 V${esc(x.version)}</button><div class="rbx"></div></div>`).join("");
  L.querySelectorAll("button[data-i]").forEach(b=>b.onclick=()=>{const x=j.items[+b.dataset.i],w=b.nextElementSibling;b.remove();
    w.innerHTML=`<div class="uwarn">從 V${esc(j.current)} 換回 V${esc(x.version)}，轉檔不會中斷。</div><div class="ubtns"><button class="btn s rbno">取消</button><button class="btn rbok">確定退回</button></div><div class="umsg"></div>`;
    w.querySelector(".rbno").onclick=()=>rbLoad();
    w.querySelector(".rbok").onclick=async()=>{const m=w.querySelector(".umsg");w.querySelector(".rbok").disabled=true;m.textContent="傳送中…";const k=await post("/api/update/rollback",{id:x.id});
      if(!k.ok){m.innerHTML=`<span class="ubad">✖ ${esc(k.message||"沒有成功")}</span>`;w.querySelector(".rbok").disabled=false;return;}w.querySelector(".ubtns").remove();updWait(m,k.to,Date.now());};});}
function updUpload(f){const r=$("#updR");if(!/\.zip$/i.test(f.name)){r.innerHTML=`<div class="ubad">✖ 請選 .zip 更新包（你選的是 ${esc(f.name)}）</div>`;return;}
  r.innerHTML=`<div class="umsg">上傳中… <span id="updP">0%</span></div><div class="ubar"><i id="updB"></i></div>`;
  const x=new XMLHttpRequest();x.open("POST",B+"/api/update/upload?name="+encodeURIComponent(f.name));x.setRequestHeader("Content-Type","application/zip");
  x.upload.onprogress=e=>{if(e.lengthComputable&&$("#updP")){const p=Math.round(e.loaded*100/e.total);$("#updP").textContent=p+"%";$("#updB").style.width=p+"%";}};
  x.onerror=()=>{if($("#updR"))$("#updR").innerHTML=`<div class="ubad">✖ 上傳失敗：連不到電腦</div>`;};
  x.onload=()=>{let j={};try{j=JSON.parse(x.responseText);}catch(e){}if(!$("#updR"))return;if(x.status===401){location.reload();return;}
    if(x.status!==200||!j.ok){$("#updR").innerHTML=`<div class="ubad">✖ ${esc(j.message||("上傳失敗（"+x.status+"）"))}</div>`;return;}j.name=f.name;updShow(j);};x.send(f);}
function updShow(j){const r=$("#updR");const tag=j.cmp>0?`<span class="utag">升級</span>`:j.cmp===0?`<span class="utag same">相同版本</span>`:`<span class="utag old">降版</span>`;
  r.innerHTML=`<div class="ucard"><div class="uvs">V${esc(j.from)} → V${esc(j.to)}${tag}</div><div class="snote" style="margin-top:4px">${esc(j.name)} · ${(j.size/1e6).toFixed(2)} MB · 已通過檢查</div>
${j.notes&&j.notes.length?`<b style="display:block;margin-top:10px;color:var(--strong)">本次更新內容</b><ul>${j.notes.map(n=>`<li>${n}</li>`).join("")}</ul>`:`<div class="snote">（這個更新包沒有附更新說明）</div>`}
${j.cmp<0?`<div class="uwarn">⚠ 這是比目前舊的版本，確定要退回嗎？</div>`:""}
<div class="ubtns" id="updBt"><button class="btn s" id="updNo">取消</button><button class="btn" id="updOk">確定更新</button></div><div class="umsg" id="updM"></div></div>`;
  $("#updNo").onclick=()=>{r.innerHTML="";};
  $("#updOk").onclick=async()=>{const m=$("#updM"),ok=$("#updOk");ok.disabled=true;m.textContent="傳送中…";let k={};try{const x=await fetch(B+"/api/update/apply",{method:"POST",cache:"no-store"});k=await x.json();}catch(e){m.innerHTML=`<span class="ubad">✖ 連不到電腦</span>`;ok.disabled=false;return;}
    if(!k.ok){m.innerHTML=`<span class="ubad">✖ ${esc(k.message||"沒有成功")}</span>`;ok.disabled=false;return;}$("#updBt").remove();updWait(m,k.to||j.to,Date.now());};}
async function updWait(m,to,t0){const sec=Math.round((Date.now()-t0)/1000);m.innerHTML=`⟳ 電腦正在更新到 V${esc(to)}…（${sec} 秒，網頁斷線幾秒是正常的）`;
  let d=null;try{d=await J("/api/update/status");}catch(e){}
  if(d&&d.current===to){m.innerHTML=`<span class="uok">✔ 已更新到 V${esc(to)}，轉檔沒有中斷。重新載入…</span>`;setTimeout(()=>location.reload(),1500);return;}
  if(sec>120){m.innerHTML=`<span class="ubad">電腦還沒回應。可能還在重新載入，請過一下再重新整理看右上角的版本號。</span>`;return;}setTimeout(()=>updWait(m,to,t0),2000);}
/* ---------------- 路由（手機的返回鍵可用） ---------------- */
const SCR={m:{},key:""};
window.addEventListener("scroll",()=>{if(SCR.key)SCR.m[SCR.key]=window.scrollY;},{passive:true});
function route(){clearTimeout(S.timer);S.pending=null;const h=(location.hash||"#quick").slice(1).split("/"),a=h[0]||"quick",arg=decodeURIComponent(h[1]||"");
  const tab=a==="bg"||a==="tool"?"tools":a;document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on",b.dataset.t===tab));
  const key=location.hash||"#quick";SCR.key="";window.scrollTo(0,0);V().onclick=null;
  if(a==="quick"){S.qk=null;viewQuick();}else if(a==="queue"){S.q=null;viewQueue();}else if(a==="outs")viewOuts();else if(a==="tools")viewTools();else if(a==="tool")viewTool(arg);else if(a==="set")viewSet();
  else if(a==="bg"){V().innerHTML='<div class="tlh"><button class="back" onclick="location.hash=\'tools\'">‹</button><b>🖥 後台狀態</b></div><div class="panel" id="bgsRoot" style="padding:10px"></div>';$("#clk").textContent="";bgsMount($("#bgsRoot"));}
  else{S.lib=null;viewLib();}
  setTimeout(()=>{SCR.key=key;const y=SCR.m[key]||0;if(y)window.scrollTo(0,y);},400);}
async function head(){try{const d=await J("/api/status");$("#ver").textContent="V"+d.version;document.title="FusionStitch・V"+d.version;}catch(e){}
  try{const o=await J("/api/outputs");const seen=+lsGet("fs_outs_seen",0),n=o.items.filter(x=>x.mtime>seen).length;const e=$("#no");if(e&&location.hash!=="#outs"){if(!seen&&o.items.length){lsSet("fs_outs_seen",String(o.items[0].mtime));e.hidden=true;}else{e.hidden=!n;e.textContent=n;}}}catch(e){}}
applyUI();window.addEventListener("hashchange",route);route();head();setInterval(()=>{if(!document.hidden)head();},30000);
setTimeout(()=>{try{sysFabInit();}catch(e){}},0);
if("serviceWorker" in navigator&&window.isSecureContext)navigator.serviceWorker.register("/sw.js").catch(()=>{});
let instEv=null;window.addEventListener("beforeinstallprompt",e=>{e.preventDefault();instEv=e;$("#inst").hidden=false;});
$("#inst").onclick=async()=>{if(!instEv)return;instEv.prompt();try{await instEv.userChoice;}catch(e){}instEv=null;$("#inst").hidden=true;};
window.addEventListener("appinstalled",()=>{$("#inst").hidden=true;toast("✔ 已安裝，之後從手機桌面的 FusionStitch 圖示開啟");});
</script></body></html>"""
