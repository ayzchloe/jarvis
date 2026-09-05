<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="csrf-token" content="{{ csrf_token() }}">
    <title>JARVIS · Voice Assistant</title>
    <style>
        /* ============================== DESIGN TOKENS ============================== */
        :root{
            --void:#03070c; --deep:#05121e;
            --panel:#0a1a2dcc; --panel-elev:#0d2339e6;
            --line:#5fdfff33; --line-hi:#5fdfff66;
            --cyan:#5fdfff; --mint:#88ffda; --amber:#ffc872; --red:#ff7a8a; --violet:#b5a8ff; --rose:#ff9ec7;
            --text:#f0fbff; --muted:#8ab4c8; --mono:ui-monospace,SFMono-Regular,Consolas,monospace;
            --glow:var(--cyan);
            --radius-sm:10px; --radius-md:16px; --radius-lg:24px;
            --shadow-sm:0 4px 16px #0006; --shadow-md:0 12px 40px #0009; --shadow-lg:0 20px 60px #000c;
        }
        @media(prefers-color-scheme: light){ /* future-proof */ }
        *{box-sizing:border-box}
        html,body{height:100%}
        body{
            min-height:100vh;margin:0;color:var(--text);
            font-family:Inter,system-ui,sans-serif;overflow-x:hidden;
            background:
                radial-gradient(ellipse 80% 50% at 50% -10%, #0a2e4a18, transparent 70%),
                radial-gradient(ellipse 60% 40% at 90% 100%, #1a1a3a14, transparent 60%),
                var(--void);
        }
        /* subtle animated grid */
        body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
            background-image:
                linear-gradient(#5fdfff0a 1px,transparent 1px),
                linear-gradient(90deg,#5fdfff0a 1px,transparent 1px);
            background-size:56px 56px;
            mask-image:radial-gradient(ellipse at 50% 35%,black 40%,transparent 85%);
            animation:gridDrift 40s linear infinite}
        @keyframes gridDrift{0%{background-position:0 0}100%{background-position:56px 56px}}
        /* vignette */
        body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:1;
            background:radial-gradient(ellipse at 50% 40%,transparent 50%,#000000c0 100%);}
        /* floating glow orbs */
        .orb{position:fixed;z-index:0;pointer-events:none;filter:blur(80px)}
        .orb.cyan{width:480px;height:480px;top:-120px;right:-100px;
            background:radial-gradient(circle,#1aaacc33,transparent 70%);animation:orbFloat 18s ease-in-out infinite}
        .orb.violet{width:400px;height:400px;bottom:-140px;left:-80px;
            background:radial-gradient(circle,#6b5bff2b,transparent 70%);animation:orbFloat 22s ease-in-out infinite reverse}
        .orb.mint{width:300px;height:300px;top:35%;left:-120px;
            background:radial-gradient(circle,#1fbf8f22,transparent 70%);animation:orbFloat 25s ease-in-out infinite}
        @keyframes orbFloat{0%,100%{transform:translate(0,0)}50%{transform:translate(30px,-20px)}}
        #bg-particles{position:fixed;inset:0;z-index:0;pointer-events:none}

        /* ============================== SHELL ============================== */
        .shell{position:relative;z-index:2;width:min(1240px,100%);min-height:100vh;margin:auto;padding:26px 22px;
            display:grid;grid-template-rows:auto 1fr auto;gap:22px}
        .shell>*{animation:fadeUp .7s cubic-bezier(.16,1,.3,1) both}
        .shell>header{animation-delay:.05s}.workspace{animation-delay:.14s}footer{animation-delay:.24s}
        /* Overlays must NEVER inherit the entrance animation: a filled
           animation's transform:none overrides the panel's own hide-transform,
           which pinned the chat permanently on screen. */
        .shell>.chat-panel,.scanline,.orb,#bg-particles{animation:none!important}

        /* ============================== TOPBAR ============================== */
        .topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
        .brand{font:650 15px var(--mono);letter-spacing:.3em;display:flex;align-items:center;gap:12px}
        .brand .sig{width:26px;height:26px;border-radius:8px;flex:none;position:relative;
            background:conic-gradient(from 210deg,var(--cyan),var(--mint),var(--cyan));
            box-shadow:0 0 18px #54ddff66,inset 0 0 8px #ffffff55;animation:sigSpin 9s linear infinite}
        .brand .sig::after{content:"";position:absolute;inset:7px;border-radius:50%;background:var(--void)}
        @keyframes sigSpin{to{transform:rotate(360deg)}}
        .brand b{color:var(--cyan);text-shadow:0 0 14px #54ddff88;animation:flicker 7s infinite}
        @keyframes flicker{0%,92%,96%,100%{opacity:1}93%,95%{opacity:.55}}
        .topright{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
        .chip{display:flex;align-items:center;gap:8px;padding:7px 12px;border:1px solid var(--line);
            border-radius:999px;background:#071523a8;font:10px var(--mono);letter-spacing:.12em;color:var(--muted);
            backdrop-filter:blur(8px)}
        .chip b{color:var(--text);font-weight:600}
        #clock{color:var(--cyan);min-width:74px;text-align:center}
        .chip-btn{cursor:pointer;color:var(--cyan);border-color:var(--line);font:600 10px var(--mono);letter-spacing:.18em;transition:.2s}
        .chip-btn:hover{border-color:var(--cyan);background:#54ddff1c;box-shadow:0 0 16px #54ddff33}
        .chip-btn.active{background:linear-gradient(135deg,#54ddff26,#83ffd21a);border-color:var(--cyan);color:var(--mint)}
        .connection{display:flex;align-items:center;gap:8px;color:var(--muted);font:10px var(--mono);letter-spacing:.12em}
        .dot{width:8px;height:8px;border-radius:50%;background:var(--amber);box-shadow:0 0 12px var(--amber);position:relative}
        .dot.online{background:var(--mint);box-shadow:0 0 12px var(--mint)}
        .dot.online::after{content:"";position:absolute;inset:-5px;border-radius:50%;border:1px solid var(--mint);animation:ping 1.8s cubic-bezier(0,0,.2,1) infinite}
        @keyframes ping{0%{transform:scale(.5);opacity:.9}80%,100%{transform:scale(1.9);opacity:0}}

        /* ============================== PANELS ============================== */
        .workspace{display:grid;grid-template-columns:250px 1fr 250px;gap:22px;align-items:stretch}
        @media(max-width:1080px){.workspace{grid-template-columns:1fr}.panel.telemetry,.panel.side{order:2}}
        .panel{position:relative;border:1px solid var(--line);border-radius:var(--radius-lg);
            background:linear-gradient(155deg,#0c2038cc,#05101fe6);
            box-shadow:var(--shadow-sm),inset 0 1px #5fdfff14;
            transition:transform .3s cubic-bezier(.16,1,.3,1),border-color .3s,box-shadow .3s}
        .panel::before{content:"";position:absolute;top:0;left:16%;right:16%;height:1px;
            background:linear-gradient(90deg,transparent,var(--cyan),transparent);opacity:.5}
        .panel:hover{transform:translateY(-4px);border-color:var(--line-hi);
            box-shadow:var(--shadow-md),inset 0 1px #5fdfff22,0 0 60px #5fdfff10}
        .telemetry,.side{padding:22px}
        .panel h2{margin:0 0 18px;color:var(--muted);font:600 10px var(--mono);letter-spacing:.2em;display:flex;align-items:center;gap:10px}
        .panel h2::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--line-hi),transparent)}

        /* ============================== METRICS ============================== */
        .metric{padding:14px 0;border-bottom:1px solid #54ddff12}
        .metric:last-child{border:0}
        .metric div:first-child{display:flex;justify-content:space-between;align-items:center;color:var(--muted);
            font:10px var(--mono);letter-spacing:.1em}
        .metric span:last-of-type{font-size:14px;color:var(--text);font-weight:600;text-shadow:0 0 16px #5fdfff55}
        .meter{height:6px;margin-top:10px;background:#081a2e;border-radius:8px;overflow:hidden;position:relative}
        .meter b{display:block;height:100%;width:0;border-radius:8px;position:relative;overflow:hidden;
            background:linear-gradient(90deg,#1aaacc,var(--cyan));
            box-shadow:0 0 14px #5fdfff88;transition:width .9s cubic-bezier(.16,1,.3,1)}
        .meter b::after{content:"";position:absolute;inset:0;transform:translateX(-100%) skewX(-15deg);
            background:linear-gradient(90deg,transparent,#ffffffcc,transparent);animation:shine 2.8s ease infinite}
        @keyframes shine{60%,100%{transform:translateX(250%) skewX(-15deg)}}
        .metric.hot .meter b{background:linear-gradient(90deg,#d64545,var(--amber));box-shadow:0 0 16px #ffc872aa}

        /* ============================== CORE ============================== */
        .core-panel{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;padding:34px 26px;text-align:center}
        .core{--scale:1;position:relative;width:min(330px,68vw);aspect-ratio:1;cursor:default;
            animation:coreBoot 1s cubic-bezier(.16,1,.3,1) both .2s}
        @keyframes coreBoot{from{opacity:0;transform:scale(.55)}to{opacity:1;transform:none}}
        .core .ring{position:absolute;border-radius:50%;pointer-events:none}
        .ring.ticks{inset:0;background:repeating-conic-gradient(var(--glow) 0deg .8deg,transparent .8deg 14deg);opacity:.16;
            -webkit-mask:radial-gradient(farthest-side,transparent calc(100% - 3px),#000 calc(100% - 2px));
            mask:radial-gradient(farthest-side,transparent calc(100% - 3px),#000 calc(100% - 2px));
            animation:spin 80s linear infinite}
        .ring.dash{inset:7%;border:1px dashed #54ddff4d;animation:spin 26s linear infinite}
        .ring.arc{inset:16%;border:2px solid transparent;border-top-color:var(--glow);border-right-color:#54ddff33;
            filter:drop-shadow(0 0 6px var(--glow));animation:spin 7s linear infinite}
        .ring.arc2{inset:27%;border:1px solid transparent;border-bottom-color:var(--mint);border-left-color:#83ffd222;
            filter:drop-shadow(0 0 5px #83ffd266);animation:spinBack 11s linear infinite}
        .ring.dots{inset:37%;border:1px dotted #83ffd240;animation:spinBack 34s linear infinite}
        @keyframes spin{to{transform:rotate(360deg)}}
        @keyframes spinBack{to{transform:rotate(-360deg)}}
        .nucleus{position:absolute;inset:41%;border-radius:50%;
            background:radial-gradient(circle at 38% 32%,#dffaff 2%,var(--glow) 32%,#12557a 72%,#072536 100%);
            box-shadow:0 0 46px color-mix(in srgb,var(--glow) 55%,transparent),
                       0 0 120px color-mix(in srgb,var(--glow) 25%,transparent),
                       inset 0 0 26px #00000090;
            display:flex;align-items:center;justify-content:center;
            transition:box-shadow .5s ease;animation:nucleusPulse 3.2s ease-in-out infinite}
        @keyframes nucleusPulse{50%{transform:scale(1.045)}}
        .wave{display:flex;align-items:center;justify-content:center;gap:3px;height:40px}
        .wave i{width:3px;height:7px;border-radius:3px;background:#04202e;transition:height .15s}
        .core.listening{--glow:var(--cyan)}
        .core.responding{--glow:var(--amber)}
        .core.thinking{--glow:var(--violet)}
        .core.error{--glow:var(--red)}
        .core.listening .nucleus,.core.responding .nucleus,.core.thinking .nucleus,.core.error .nucleus{
            background:radial-gradient(circle at 38% 32%,#fff 2%,var(--glow) 36%,#0e3f5e 74%,#06202f 100%)}
        .core.listening .wave i{animation:wave .7s ease-in-out infinite}
        .core.responding .wave i{animation:wave .55s ease-in-out infinite}
        .core.thinking .wave i{animation:wave 1s ease-in-out infinite}
        .core.error .nucleus{animation:none}
        .core.thinking .arc{animation-duration:2.4s}
        .core.thinking .dash{animation-duration:9s}
        .wave i:nth-child(1){animation-delay:0s}.wave i:nth-child(2){animation-delay:.09s}
        .wave i:nth-child(3){animation-delay:.18s}.wave i:nth-child(4){animation-delay:.27s}
        .wave i:nth-child(5){animation-delay:.36s}.wave i:nth-child(6){animation-delay:.45s}
        .wave i:nth-child(7){animation-delay:.54s}
        @keyframes wave{0%,100%{height:7px}50%{height:22px}}
        .state{font:600 12px var(--mono);letter-spacing:.4em;color:var(--text);position:relative;padding-bottom:10px}
        .state::after{content:"";position:absolute;left:50%;bottom:0;transform:translateX(-50%);width:64px;height:2px;
            background:linear-gradient(90deg,transparent,var(--cyan),transparent);
            background-size:200% 100%;animation:stateScan 2.6s linear infinite}
        @keyframes stateScan{to{background-position:-200% 0}}
        .last-action{max-width:520px;min-height:40px;color:var(--muted);font:12.5px/1.55 system-ui,sans-serif}
        .voice-button{margin-top:4px;position:relative;overflow:hidden;padding:14px 42px;border:none;border-radius:999px;
            font:700 12px var(--mono);letter-spacing:.22em;color:#03212e;cursor:pointer;
            background:linear-gradient(135deg,var(--cyan),#2ac4d9);
            box-shadow:0 10px 34px #54ddff4d,0 0 0 1px #54ddff66,inset 0 1px #ffffff88;
            transition:transform .18s ease,box-shadow .25s ease}
        .voice-button:hover{transform:translateY(-2px);box-shadow:0 16px 46px #54ddff66,0 0 0 1px var(--cyan)}
        .voice-button:active{transform:scale(.96)}
        .voice-button.active{background:linear-gradient(135deg,var(--mint),#39d6c0);color:#03291f;
            box-shadow:0 10px 40px #83ffd255,0 0 0 1px #83ffd288;animation:breathGlow 2.2s ease-in-out infinite}
        @keyframes breathGlow{50%{box-shadow:0 10px 52px #83ffd277,0 0 0 1px var(--mint)}}
        .voice-button::before{content:"";position:absolute;top:0;bottom:0;width:46%;left:-60%;transform:skewX(-20deg);
            background:linear-gradient(90deg,transparent,#ffffff70,transparent);animation:sheen 4.5s ease infinite}
        @keyframes sheen{55%,100%{left:130%}}
        .ripple{position:absolute;border-radius:50%;background:#ffffff66;transform:scale(0);
            animation:rip .55s ease-out forwards;pointer-events:none}
        @keyframes rip{to{transform:scale(3.2);opacity:0}}
        .voice-note{font:9px var(--mono);letter-spacing:.28em;color:var(--muted)}
        .confirmation{width:100%;max-width:480px;margin-top:8px;padding:14px 18px;border-radius:14px;display:none;
            border:1px solid #ffba6155;background:#211305d9;color:#ffe3ba;font:12px/1.5 system-ui,sans-serif;
            border-left:3px solid var(--amber)}
        .confirmation.visible{display:block;animation:confIn .4s cubic-bezier(.16,1,.3,1),shake .5s ease .1s}
        @keyframes confIn{from{opacity:0;transform:translateY(-12px)}}
        @keyframes shake{20%,60%{transform:translateX(-5px)}40%,80%{transform:translateX(5px)}}

        /* ============================== SIDE LISTS ============================== */
        .side-section{margin-top:22px}
        .task,.connector{display:flex;gap:11px;align-items:flex-start;padding:11px 12px;margin-bottom:9px;
            border:1px solid #54ddff17;border-radius:13px;background:#08182773;cursor:default;
            transition:transform .18s ease,border-color .18s ease,background .18s ease;
            animation:listIn .45s ease both}
        @keyframes listIn{from{opacity:0;transform:translateX(14px)}}
        .task:nth-child(odd){animation-delay:.04s}
        .task:hover,.connector:hover{transform:translateX(5px);border-color:var(--line-hi);background:#0a2033a8}
        .task i,.connector>i{flex:none;width:8px;height:8px;border-radius:50%;margin-top:6px;background:#315063;transition:.2s}
        .task i,.connector i.ready{background:var(--mint);box-shadow:0 0 10px var(--mint)}
        .task b,.connector b{display:block;font:600 12px system-ui,sans-serif;color:var(--text)}
        .task small,.connector small{color:var(--muted);font:10px system-ui,sans-serif}
        .connector{flex-wrap:wrap}
        .connector>div{flex:1;min-width:0}
        .connector-acc{display:block;font:10px var(--mono);color:var(--mint);margin-top:2px}
        .connector-btn{border:1px solid var(--line);background:transparent;color:var(--cyan);border-radius:8px;
            padding:6px 12px;font:600 9px var(--mono);letter-spacing:.12em;cursor:pointer;transition:.18s;position:relative;overflow:hidden}
        .connector-btn:hover{background:#5fdfff1a;border-color:var(--cyan);transform:translateY(-1px)}
        .connector-btn.disconnect{color:var(--red);border-color:#ff7a8a44}
        .connector-btn.disconnect:hover{background:#ff7a8a1a;border-color:var(--red)}
        .agent{display:flex;gap:12px;align-items:center;padding:12px 14px;margin-bottom:10px;
            background:linear-gradient(135deg,#0e223a88,#071422cc);
            border:1px solid var(--line);border-radius:var(--radius-md);cursor:pointer;
            transition:border-color .2s,transform .2s,box-shadow .2s,background .2s}
        .agent:hover{border-color:var(--amber);transform:translateX(4px);
            box-shadow:0 8px 28px #0008,0 0 0 1px var(--amber)33}
        .agent>div{min-width:0}
        .agent b{display:block;font:600 11px var(--mono);letter-spacing:.06em;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .agent small{display:block;font:9px var(--mono);color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .agent-btn{border:1px solid var(--line);background:transparent;color:var(--amber);border-radius:8px;
            padding:6px 12px;font:600 9px var(--mono);letter-spacing:.12em;cursor:pointer;transition:.18s;flex-shrink:0}
        .agent-btn:hover{background:#ffc8721a;border-color:var(--amber);transform:translateY(-1px)}

        /* ============================== MODEL SELECTOR ============================== */
        .model-selector-wrap{display:flex;justify-content:center;margin-top:14px}
        .model-selector{position:relative}
        .model-btn{display:flex;align-items:center;gap:9px;padding:11px 20px;border-radius:12px;
            background:rgba(9,23,37,.92);border:1px solid var(--line);color:var(--text);
            font:600 12px var(--mono);letter-spacing:.06em;cursor:pointer;transition:all .2s;position:relative;overflow:hidden}
        .model-btn:hover{border-color:var(--cyan);background:rgba(10,29,46,.96);transform:translateY(-1px);
            box-shadow:0 8px 26px #54ddff26}
        .model-btn svg{flex-shrink:0;transition:transform .25s}
        .model-btn.open svg{transform:rotate(180deg)}
        .model-dropdown{position:absolute;top:calc(100% + 8px);right:0;min-width:250px;z-index:101;
            background:linear-gradient(180deg,#07131f,#030a12);border:1px solid var(--line);border-radius:14px;
            box-shadow:0 18px 60px #000d,0 0 30px #54ddff14;overflow:hidden auto;max-height:min(430px,70vh);
            opacity:0;visibility:hidden;transform:translateY(-10px) scale(.97);transform-origin:top right;
            transition:all .22s cubic-bezier(.16,1,.3,1)}
        .model-dropdown.open{opacity:1;visibility:visible;transform:none}
        .model-section{padding:9px}
        .model-section:not(:last-child){border-bottom:1px solid #54ddff14}
        .model-section h4{margin:4px 0 7px;padding:0 10px;font:500 9.5px var(--mono);letter-spacing:.18em;
            color:var(--muted);text-transform:uppercase}
        .model-option{display:block;width:100%;padding:10px 12px;border-radius:9px;background:transparent;border:none;
            color:var(--text);font:12px system-ui,sans-serif;text-align:left;cursor:pointer;position:relative;
            transition:all .14s}
        .model-option:hover{background:#54ddff14;color:var(--cyan);transform:translateX(3px)}
        .model-option.active{background:#54ddff1f;color:var(--cyan);font-weight:600}
        .model-option.active::before{content:"";position:absolute;left:0;top:22%;bottom:22%;width:3px;border-radius:3px;
            background:var(--cyan);box-shadow:0 0 8px var(--cyan)}

        /* ============================== FOOTER ============================== */
        footer{text-align:center;font:9.5px var(--mono);letter-spacing:.22em;color:var(--muted)}
        footer b{color:var(--cyan)}

        /* ============================== CHAT CONSOLE ==============================
           Compact glass console floating over the core. Opens on demand
           (voice: "open jarvis chat" · CHAT chip · Ctrl+K). JARVIS routes
           long answers here and only speaks a short confirmation. */
        .chat-panel{position:fixed;left:50%;bottom:128px;z-index:1200;width:min(470px,94vw);
            max-height:min(580px,74vh);display:flex;flex-direction:column;
            border-radius:22px;border:1px solid var(--line-hi);
            background:linear-gradient(168deg,#0a1e30f2,#040d18fa);
            box-shadow:0 34px 110px #000e,0 0 0 1px #54ddff1c,0 0 70px #54ddff12;
            opacity:0;visibility:hidden;pointer-events:none;
            transform:translateX(-50%) translateY(26px) scale(.95);transform-origin:bottom center;
            transition:opacity .28s ease,transform .32s cubic-bezier(.16,1,.3,1),visibility .28s}
        .chat-panel.open{opacity:1;visibility:visible;pointer-events:auto;
            transform:translateX(-50%) translateY(0) scale(1)}
        .chat-panel::before{content:"";position:absolute;top:0;left:14%;right:14%;height:1px;z-index:1;
            background:linear-gradient(90deg,transparent,var(--cyan),transparent)}
        .chat-header{padding:13px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;
            justify-content:space-between;background:#071523d9;border-radius:22px 22px 0 0}
        .chat-title{display:flex;align-items:center;gap:9px;font:600 11.5px var(--mono);letter-spacing:.26em;color:var(--cyan)}
        .live-dot{width:7px;height:7px;border-radius:50%;background:var(--mint);box-shadow:0 0 10px var(--mint)}
        .chat-close{width:30px;height:30px;border-radius:9px;background:transparent;border:1px solid var(--line);
            color:var(--muted);font:600 13px var(--mono);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.18s}
        .chat-close:hover{background:var(--red);border-color:var(--red);color:#03121a;transform:rotate(90deg)}
        .chat-messages{flex:1;min-height:130px;overflow-y:auto;padding:16px 18px;display:flex;flex-direction:column;gap:11px}
        .chat-message{display:flex;gap:10px;max-width:88%;animation:msgIn .3s cubic-bezier(.16,1,.3,1) both}
        @keyframes msgIn{from{opacity:0;transform:translateY(10px) scale(.96)}}
        .chat-message.user{flex-direction:row-reverse;align-self:flex-end}
        .chat-message.assistant{align-self:flex-start}
        .chat-avatar{width:29px;height:29px;border-radius:50%;display:flex;align-items:center;justify-content:center;
            font:600 11px var(--mono);flex-shrink:0;color:#03212e;
            background:linear-gradient(135deg,var(--cyan),var(--mint));box-shadow:0 0 12px #54ddff44}
        .chat-message.user .chat-avatar{background:linear-gradient(135deg,#2b4a63,#17364c);color:#bfe9ff;box-shadow:none}
        .chat-bubble{padding:11px 14px;border-radius:14px;font:13px/1.55 system-ui,sans-serif;white-space:pre-wrap;
            word-break:break-word;background:#0a1e30d9;border:1px solid #54ddff22}
        .chat-message.user .chat-bubble{background:linear-gradient(135deg,#0e3450,#0a2438);border-color:#54ddff44;
            border-bottom-right-radius:4px}
        .chat-message.assistant .chat-bubble{border-bottom-left-radius:4px}
        .chat-message.error .chat-bubble{border-color:#ff657755;background:#260a10d0;color:#ffc9d1}
        .typing-bubble{display:inline-flex;gap:5px;padding:14px 16px}
        .typing-bubble i{width:6px;height:6px;border-radius:50%;background:var(--cyan);opacity:.5;
            animation:typeDot 1.1s ease-in-out infinite}
        .typing-bubble i:nth-child(2){animation-delay:.15s}.typing-bubble i:nth-child(3){animation-delay:.3s}
        @keyframes typeDot{40%{transform:translateY(-5px);opacity:1}}
        .chat-input-wrap{padding:14px 18px 16px;border-top:1px solid var(--line)}
        .chat-input-row{display:flex;gap:9px}
        .chat-input{flex:1;padding:12px 15px;border-radius:12px;border:1px solid var(--line);outline:none;
            background:#071523b8;color:var(--text);font:13px system-ui,sans-serif;transition:.2s}
        .chat-input:focus{border-color:var(--cyan);box-shadow:0 0 0 3px #54ddff1f,0 0 18px #54ddff22}
        .chat-input::placeholder{color:#4f7085}
        .chat-send{width:44px;height:44px;flex:none;border:none;border-radius:12px;cursor:pointer;color:#03212e;
            display:flex;align-items:center;justify-content:center;
            background:linear-gradient(135deg,var(--cyan),#2ac4d9);
            box-shadow:0 6px 20px #54ddff3d;transition:.2s}
        .chat-send svg{width:18px;height:18px;transition:transform .25s}
        .chat-send:hover{transform:translateY(-2px);box-shadow:0 10px 28px #54ddff55}
        .chat-send:hover svg{transform:translateX(2px) rotate(-12deg)}
        .chat-send:disabled{opacity:.45;cursor:wait;transform:none}
        .chat-hint{display:block;margin-top:9px;font:9.5px var(--mono);letter-spacing:.12em;color:var(--muted)}

        /* ============================== SCROLLBARS / MISC ============================== */
        ::-webkit-scrollbar{width:8px;height:8px}
        ::-webkit-scrollbar-track{background:transparent}
        ::-webkit-scrollbar-thumb{background:#54ddff2e;border-radius:8px;border:2px solid transparent;background-clip:content-box}
        ::-webkit-scrollbar-thumb:hover{background:#54ddff55;border:2px solid transparent;background-clip:content-box}
        ::selection{background:#54ddff44;color:#fff}

        /* ============================== CUTE AGENT FACES ============================== */
        .agent-face{width:44px;height:44px;flex:none;display:flex;align-items:center;justify-content:center;
            filter:drop-shadow(0 4px 14px #0006);transition:transform .3s cubic-bezier(.16,1,.3,1)}
        .agent:hover .agent-face{transform:scale(1.08) rotate(-3deg)}
        .agent.running .agent-face{animation:agentBounce 1.2s ease-in-out infinite}
        @keyframes agentBounce{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-6px) rotate(3deg)}}
        /* face variants by agent id */
        .agent-face[data-id="lead_hunter"]{background:linear-gradient(135deg,#ffd6a5,#ffb347);border-radius:50%}
        .agent-face[data-id="calendar_commander"]{background:linear-gradient(135deg,#a8e6ff,#5fdfff);border-radius:12px}
        .agent-face[data-id="inbox_zero"]{background:linear-gradient(135deg,#b8ffd9,#88ffda);border-radius:50% 50% 50% 50% / 60% 60% 40% 40%}
        .agent-face[data-id="invoice_ivy"]{background:linear-gradient(135deg,#ffd1e8,#ff9ec7);border-radius:50%}
        .agent-face[data-id="recruiter_ryan"]{background:linear-gradient(135deg,#e8d5ff,#b5a8ff);border-radius:14px}

        /* ============================== AGENT CARDS ============================== */
        .agent{position:relative;overflow:hidden;background:
            linear-gradient(135deg,#0d1f36cc,#061222e6);
            border-color:var(--line)}
        .agent::before{content:"";position:absolute;inset:0;pointer-events:none;
            background:radial-gradient(140px 70px at 15% 0%,var(--amber)15,transparent 70%);opacity:0;transition:.3s}
        .agent:hover::before{opacity:1}
        .agent.running{border-color:var(--amber);animation:agentPulse 2s ease-in-out infinite}
        @keyframes agentPulse{0%,100%{box-shadow:0 0 0 0 var(--amber)25}50%{box-shadow:0 0 30px 6px var(--amber)15}}
        .agent .face-wrap{position:relative;flex:none;width:52px;height:52px}
        .agent .status-dot{position:absolute;right:-2px;bottom:-2px;width:14px;height:14px;border:3px solid var(--void);background:var(--mint);
            box-shadow:0 0 12px var(--mint);border-radius:50%;animation:dotBlink 2.2s ease-in-out infinite}
        .agent.running .status-dot{background:var(--amber);box-shadow:0 0 16px var(--amber);animation:dotBlink 1s ease-in-out infinite}
        @keyframes dotBlink{0%,100%{opacity:1}50%{opacity:.4}}
        .agent .run-badge{display:inline-flex;align-items:center;gap:4px;margin-left:8px;padding:2px 8px;border-radius:999px;
            font:600 8px var(--mono);letter-spacing:.1em;color:#ffd6a5;background:#ffb34722;border:1px solid var(--amber)44}
        .agent .run-badge.live{color:#88ffda;background:#5fdfff1a;border-color:var(--cyan)55;animation:dotBlink 1.2s infinite}
        .agent-btn{position:relative;z-index:1}
        .agent-btn.running{color:var(--cyan);border-color:var(--cyan);background:#5fdfff1a}
        .agent-scan{position:absolute;right:0;top:0;bottom:0;width:3px;background:linear-gradient(var(--amber),transparent);
            opacity:0;transition:.3s}
        .agent.running .agent-scan{opacity:1;animation:agentScan 1.2s linear infinite}
        @keyframes agentScan{0%{transform:translateY(-100%)}100%{transform:translateY(100%)}}

        /* ============================== LIVE AGENT RUN FEED ============================== */
        .agent-run{display:flex;flex-direction:column;gap:8px;min-width:0;padding:4px 0}
        .agent-run .run-title{font:600 9px var(--mono);letter-spacing:.15em;color:var(--amber);display:flex;align-items:center;gap:8px}
        .agent-run .run-title::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--amber);
            box-shadow:0 0 10px var(--amber);animation:dotBlink 1s infinite}
        .agent-run .run-track{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
        .run-step{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;
            font:600 8px var(--mono);letter-spacing:.1em;border:1px solid var(--line);color:var(--muted);opacity:.5;transition:.3s}
        .run-step.on{opacity:1;color:var(--cyan);border-color:var(--cyan);background:#5fdfff12}
        .run-step.done{opacity:1;color:var(--mint);border-color:var(--mint);background:#88ffda10}
        .run-step.fail{opacity:1;color:var(--red);border-color:var(--red);background:#ff7a8a12}
        .run-step i{width:5px;height:5px;border-radius:50%;background:currentColor}
        .run-step.on i{animation:dotBlink .7s infinite}
        .agent-run .log-line{font:10px var(--mono);color:var(--muted);overflow-wrap:anywhere;line-height:1.5}
        .agent-run .log-line.ok{color:var(--mint)}
        .agent-run .log-line.err{color:var(--red)}
        .chat-message.agent .chat-avatar{width:32px;height:32px;border-radius:50%;
            background:linear-gradient(135deg,#ffd6a5,#ffb347);color:#2b1604;box-shadow:0 0 16px #ffb34755}
        .chat-message.agent .chat-bubble{border-color:var(--amber)55;background:#1e160ad9}
        .chat-message.agent.error .chat-bubble{border-color:var(--red)55;background:#2a0a10d9;color:#ffc9d1}

        /* ============================== POLISH ============================== */
        .core-panel{border-color:var(--cyan)33;box-shadow:inset 0 1px 0 #5fdfff18,var(--shadow-md)}
        .side-panel{box-shadow:var(--shadow-md)}
        .panel h3 small{opacity:.55;transition:.2s}
        .panel h3:hover small{opacity:1}
        @media(prefers-reduced-motion:reduce){
            *,*::before,*::after{animation-duration:.001s!important;animation-iteration-count:1!important;transition-duration:.001s!important}
        }
</style>
</head>
<body>
<canvas id="bg-particles"></canvas>
<div class="orb cyan"></div><div class="orb violet"></div><div class="orb mint"></div>
<div class="scanline"></div>

<main class="shell">
    <header class="topbar">
        <div class="brand"><span class="sig"></span><span><b>J.A.R.V.I.S.</b>&nbsp;VOICE ASSISTANT</span></div>
        <div class="topright">
            <span class="chip" id="memory-count">MEMORY 0</span>
            <span class="chip"><b id="clock">--:--:--</b></span>
            <button class="chip chip-btn" id="chat-open-btn" title="Toggle chat (Ctrl+K)" onclick="window.__jarvisChat?window.__jarvisChat.toggle():document.getElementById('chat-panel').classList.toggle('open')">CHAT</button>
            <div class="connection"><i class="dot" id="health-dot"></i><span id="health-text">CONNECTING</span></div>
        </div>
    </header>

    <section class="workspace">
        <aside class="panel telemetry">
            <h2>LIVE SYSTEM</h2>
            <div class="metric" id="m-cpu"><div><span>CPU</span><span id="cpu-value">--</span></div><div class="meter"><b id="cpu-meter"></b></div></div>
            <div class="metric" id="m-ram"><div><span>MEMORY</span><span id="ram-value">--</span></div><div class="meter"><b id="ram-meter"></b></div></div>
            <div class="metric" id="m-disk"><div><span>STORAGE</span><span id="disk-value">--</span></div><div class="meter"><b id="disk-meter"></b></div></div>
            <div class="metric" id="m-battery"><div><span>BATTERY</span><span id="battery-value">--</span></div><div class="meter"><b id="battery-meter"></b></div></div>
        </aside>

        <section class="panel core-panel">
            <div class="core" id="core">
                <i class="ring ticks"></i><i class="ring dash"></i><i class="ring arc"></i>
                <i class="ring arc2"></i><i class="ring dots"></i>
                <div class="nucleus"><span class="wave"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span></div>
            </div>
            <div class="state" id="state">VOICE STANDBY</div>
            <div class="last-action" id="last-action">Press activate once. JARVIS keeps listening after every response.</div>
            <button class="voice-button" id="voice-control">ACTIVATE VOICE</button>
            <div class="voice-note">SAY “STOP LISTENING” TO PAUSE</div>
            <div class="confirmation" id="confirmation"></div>
            <!-- Model Selector -->
            <div class="model-selector-wrap">
                <div class="model-selector" id="model-selector">
                    <button class="model-btn" id="model-btn" aria-label="Select model" title="Current model">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9z"/>
                        </svg>
                        <span id="model-name">Groq · GPT-OSS 20B</span>
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M6 9l6 6 6-6"/>
                        </svg>
                    </button>
                    <div class="model-dropdown" id="model-dropdown">
                        <small style="display:block;padding:12px;color:var(--muted);font:11px var(--mono)">Loading models…</small>
                    </div>
                </div>
            </div>
        </section>

        <aside class="panel side">
            <h2>FOCUS QUEUE</h2>
            <div id="tasks"><small style="color:var(--muted)">Loading focus queue</small></div>
            <div class="side-section"><h2>CONNECTORS</h2><div id="connectors"><small style="color:var(--muted)">Checking local configuration</small></div></div>
            <div class="side-section"><h2>SKILLS</h2><div id="skills"><small style="color:var(--muted)">Synchronizing skills</small></div></div>
            <div class="side-section"><h2>AGENTS</h2><div id="agents"><small style="color:var(--muted)">Connecting to agent hub</small></div></div>
        </aside>
    </section>

    <footer><b>LOCAL VOICE CONTROL</b> · DANGEROUS POWER ACTIONS REQUIRE A VOICE CONFIRMATION · HUD BUILD 2026-08-29.2</footer>
</main>

    <!-- Chat Panel (closed by default — open via the CHAT chip or Ctrl+K).
         Lives OUTSIDE .shell so no parent animation/transform can ever
         affect its fixed positioning or hidden state. -->
    <div class="chat-panel" id="chat-panel" role="dialog" aria-label="JARVIS Chat">
        <div class="chat-header">
            <span class="chat-title"><i class="live-dot"></i>J.A.R.V.I.S.</span>
            <button class="chat-close" id="chat-close" aria-label="Close chat" title="Close (Esc)" onclick="window.__jarvisChat?window.__jarvisChat.close():this.closest('.chat-panel').classList.remove('open')">×</button>
        </div>
        <div class="chat-messages" id="chat-messages"></div>
        <div class="chat-input-wrap">
            <div class="chat-input-row">
                <input type="text" class="chat-input" id="chat-input" placeholder="Type a command..." autocomplete="off" aria-label="Command input">
                <button class="chat-send" id="chat-send" aria-label="Send command">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>
                </button>
            </div>
            <span class="chat-hint">TYPE A COMMAND · ENTER TO SEND · ESC TO CLOSE</span>
        </div>
    </div>
<script>
    const token=document.querySelector('meta[name="csrf-token"]').content,core=document.querySelector('#core'),state=document.querySelector('#state'),lastAction=document.querySelector('#last-action'),voiceControl=document.querySelector('#voice-control'),confirmation=document.querySelector('#confirmation'),createSessionId=()=>crypto.randomUUID?.()||`${Date.now()}-${Math.random()}`,sessionId=(()=>{try{const saved=localStorage.getItem('jarvis.voice.session');if(saved)return saved;const created=createSessionId();localStorage.setItem('jarvis.voice.session',created);return created}catch{return createSessionId()}})();
    const headers={'Content-Type':'application/json','Accept':'application/json','X-CSRF-TOKEN':token,'X-Jarvis-Session':sessionId};

    let listening=false,continuousMode=false,requestInFlight=false,isSpeaking=false,micMutedUntil=0,pendingApproval=null,recognition=null,currentUtterance=null,speechTimer=null,settleTimer=null,watchdog=null,micStream=null,accumulatedTranscript='',interimText='',lastSpoken='';

    // ===== CHAT PANEL (bound first — nothing above can break it) =====
    const chatPanel=document.getElementById('chat-panel'),chatClose=document.getElementById('chat-close'),chatMessages=document.getElementById('chat-messages'),chatInput=document.getElementById('chat-input'),chatSend=document.getElementById('chat-send'),chatOpenBtn=document.getElementById('chat-open-btn');
    let chatOpen=false;

    function syncChat(open){
        chatOpen=open;
        chatPanel.classList.toggle('open',open);
        if(chatOpenBtn)chatOpenBtn.classList.toggle('active',open);
    }
    function openChat(){syncChat(true);try{chatInput.focus();}catch(e){}}
    function closeChat(){syncChat(false);}
    function toggleChat(){chatOpen?closeChat():openChat();}
    /* Global escape hatches: inline onclick handlers work even if the main
       script dies, so the chat can ALWAYS be closed. */
    window.__jarvisChat={open:openChat,close:closeChat,toggle:toggleChat};

    chatOpenBtn&&chatOpenBtn.addEventListener('click',e=>{e.stopPropagation();toggleChat();});
    chatClose.addEventListener('click',closeChat);
    // Delegated fallback — immune to any runtime error elsewhere on the page
    document.addEventListener('click',e=>{
        if(e.target.closest&&e.target.closest('#chat-close')){closeChat();return;}
        // Click anywhere outside an open panel dismisses it.
        if(chatOpen&&e.target.closest&&!e.target.closest('#chat-panel')&&!e.target.closest('#chat-open-btn'))closeChat();
    });
    document.addEventListener('keydown',e=>{
        if((e.ctrlKey||e.metaKey)&&e.key==='k'){e.preventDefault();toggleChat();}
        else if(e.key==='Escape'&&chatOpen){closeChat();}
    });

    function appendChatMessage(role,text,isError=false,node=null){
        const wrap=document.createElement('div');
        wrap.className='chat-message '+role+(isError?' error':'');
        const avatar=document.createElement('div');
        avatar.className='chat-avatar';
        avatar.textContent=role==='user'?'U':role==='agent'?'A':'J';
        const bubble=node||document.createElement('div');
        bubble.className=node?'':'chat-bubble';
        if(!node)bubble.textContent=text;
        if(role==='user'){wrap.append(bubble,avatar);}else{wrap.append(avatar,bubble);}
        chatMessages.append(wrap);
        chatMessages.scrollTop=chatMessages.scrollHeight;
        return wrap;
    }

    function showTyping(){
        hideTyping();
        const wrap=document.createElement('div');
        wrap.className='chat-message assistant';
        wrap.id='chat-typing';
        const avatar=document.createElement('div');
        avatar.className='chat-avatar';avatar.textContent='J';
        const bubble=document.createElement('div');
        bubble.className='chat-bubble typing-bubble';
        bubble.innerHTML='<i></i><i></i><i></i>';
        wrap.append(avatar,bubble);
        chatMessages.append(wrap);
        chatMessages.scrollTop=chatMessages.scrollHeight;
    }
    function hideTyping(){const t=document.getElementById('chat-typing');if(t)t.remove();}

    async function sendChatMessage(){
        const text=chatInput.value.trim();
        if(!text||requestInFlight) return;
        chatInput.value='';
        chatSend.disabled=true;

        const routed=tryRouteAgentTask(text);
        if(routed){
            requestInFlight=true;
            try{ await runAgent(routed.agent,text); }catch(e){}
            requestInFlight=false;
            chatSend.disabled=false;
            chatInput.focus();
            return;
        }

        appendChatMessage('user',text);
        showTyping();
        setAction('Working…');
        setState('thinking','WORKING');
        requestInFlight=true;
        try{
            const res=await fetch('/api/command',{method:'POST',headers,body:JSON.stringify({text})});
            const data=await res.json();
            if(data.open_chat)openChat();
            if(data.close_chat)closeChat();
            hideTyping();
            if(!res.ok)throw new Error(data.message||data.detail||'Request failed');
            if(data.stats)updateStats(data.stats);
            if(data.tasks)updateTasks(data.tasks);
            if(data.skills)updateSkills(data.skills);
            if(data.memory_count!==undefined)updateMemory(data.memory_count);
            if(data.confirmations?.length){
                requestInFlight=false;
                showApproval(data.confirmations[0]);
                appendChatMessage('assistant','Confirmation needed — check the center panel.');
                return;
            }
            const shown=data.chat_text||data.reply||'Completed.';
            appendChatMessage('assistant',shown);
            setAction(data.reply||'Completed.');
            speak(data.reply||'Completed.');
        }catch(err){
            hideTyping();
            appendChatMessage('assistant','Error: '+err.message,true);
            setAction('Error: '+err.message);
            setState('error','ERROR');
        }finally{
            requestInFlight=false;
            chatSend.disabled=false;
            chatInput.focus();
        }
    }

    chatSend.addEventListener('click',sendChatMessage);
    chatInput.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendChatMessage();}});

    // ===== AMBIENT FX (guarded + throttled — can never break functionality) =====
    try{
        const canvas=document.getElementById('bg-particles'),ctx=canvas.getContext('2d');
        let W,H,stars=[],mx=.5,my=.5,lastFrame=0;
        const resize=()=>{W=canvas.width=innerWidth;H=canvas.height=innerHeight};
        resize();addEventListener('resize',resize);
        addEventListener('pointermove',e=>{mx=e.clientX/W;my=e.clientY/H},{passive:true});
        const N=Math.min(55,Math.floor(innerWidth*innerHeight/26000));
        for(let i=0;i<N;i++)stars.push({x:Math.random(),y:Math.random(),z:.3+Math.random()*.7,r:.4+Math.random()*1.4,tw:Math.random()*Math.PI*2,sp:.008+Math.random()*.02});
        (function draw(ts){
            requestAnimationFrame(draw);
            if(document.hidden)return;
            if(ts-lastFrame<33)return; /* ~30fps cap */
            lastFrame=ts;
            ctx.clearRect(0,0,W,H);
            for(const s of stars){
                s.y-=s.sp*s.z;if(s.y<-.02)s.y=1.02;
                s.tw+=.02;
                const px=(s.x+(mx-.5)*.03*s.z)*W,py=(s.y+(my-.5)*.03*s.z)*H;
                ctx.globalAlpha=.22+.5*Math.abs(Math.sin(s.tw))*s.z;
                ctx.fillStyle=s.z>.75?'#9fe8ff':'#5b8db4';
                ctx.beginPath();ctx.arc(px,py,s.r*s.z*1.6,0,7);ctx.fill();
            }
        })(0);
    }catch(e){}
    setInterval(()=>{const el=document.getElementById('clock');if(el)el.textContent=new Date().toLocaleTimeString([],{hour12:false})},1000);

    document.addEventListener('pointerdown',e=>{ // click ripples
        try{
            const btn=e.target.closest&&e.target.closest('.voice-button,.model-btn,#chat-send,.connector-btn');
            if(!btn)return;
            const rect=btn.getBoundingClientRect(),d=Math.max(rect.width,rect.height)*1.2;
            const span=document.createElement('span');span.className='ripple';
            span.style.cssText=`width:${d}px;height:${d}px;left:${e.clientX-rect.left-d/2}px;top:${e.clientY-rect.top-d/2}px`;
            btn.appendChild(span);setTimeout(()=>span.remove(),600);
        }catch(err){}
    });

    // ===== MODEL SELECTOR =====
    const modelBtn=document.getElementById('model-btn'),modelDropdown=document.getElementById('model-dropdown'),modelName=document.getElementById('model-name');
    let currentProvider='groq',currentModel='openai/gpt-oss-20b';

    const prettyModel=id=>{
        const known={'gemini-3.7-flash':'Gemini 3.7 Flash','gemini-3.5-flash':'Gemini 3.5 Flash','gemini-3.5-flash-lite':'Gemini 3.5 Lite','gemini-2.5-flash':'Gemini 2.5 Flash'};
        if(known[id])return known[id];
        if(id.includes('gpt-oss-120b'))return'GPT-OSS 120B';
        if(id.includes('gpt-oss-20b'))return'GPT-OSS 20B';
        return id.split('/').pop();
    };

    function renderModelOptions(models){
        modelDropdown.replaceChildren();
        Object.entries(models||{}).forEach(([provider,list])=>{
            if(!list?.length)return;
            const section=document.createElement('div');
            section.className='model-section';
            const title=document.createElement('h4');
            title.textContent=provider==='groq'?'Groq':'Gemini';
            section.append(title);
            list.forEach((m,i)=>{
                const btn=document.createElement('button');
                btn.className='model-option';
                btn.dataset.provider=provider;
                btn.dataset.model=m.id;
                btn.textContent=m.name&&m.name!==m.id?m.name:prettyModel(m.id);
                btn.title=m.description||m.id;
                btn.style.animation=`listIn .3s ease both ${i*.04}s`;
                btn.addEventListener('click',()=>selectModel(provider,m.id));
                section.append(btn);
            });
            modelDropdown.append(section);
        });
        if(!modelDropdown.children.length){
            modelDropdown.innerHTML='<small style="display:block;padding:12px;color:var(--red);font:11px var(--mono)">No models available</small>';
        }
        highlightOption(currentProvider,currentModel);
    }

    async function initModelSelector(){
        modelBtn.addEventListener('click',e=>{e.stopPropagation();toggleDropdown();});
        document.addEventListener('click',()=>closeDropdown());
        modelDropdown.addEventListener('click',e=>e.stopPropagation());
        try{
            const [modelsRes,currentRes]=await Promise.all([fetch('/api/models',{headers}),fetch('/api/model/get',{headers})]);
            if(modelsRes.ok)renderModelOptions(await modelsRes.json());
            else modelDropdown.innerHTML='<small style="display:block;padding:12px;color:var(--red);font:11px var(--mono)">Backend offline — start main.py</small>';
            if(currentRes.ok){
                const cur=await currentRes.json();
                if(cur.provider&&cur.model){currentProvider=cur.provider;currentModel=cur.model;}
            }
        }catch{
            modelDropdown.innerHTML='<small style="display:block;padding:12px;color:var(--red);font:11px var(--mono)">Could not load models</small>';
        }
        updateModelButton();
        highlightOption(currentProvider,currentModel);
    }

    function toggleDropdown(){modelDropdown.classList.toggle('open');modelBtn.classList.toggle('open');}
    function closeDropdown(){modelDropdown.classList.remove('open');modelBtn.classList.remove('open');}

    function selectModel(provider,model){
        currentProvider=provider; currentModel=model;
        updateModelButton(); highlightOption(provider,model); closeDropdown();
        setAction('Model: '+(provider==='groq'?'Groq':'Gemini')+' · '+prettyModel(model));
        fetch('/api/model/set',{method:'POST',headers,body:JSON.stringify({provider,model})}).catch(()=>{});
    }

    function updateModelButton(){
        modelName.textContent=(currentProvider==='groq'?'Groq':'Gemini')+' · '+prettyModel(currentModel);
    }

    function highlightOption(provider,model){
        modelDropdown.querySelectorAll('.model-option').forEach(opt=>{
            opt.classList.toggle('active',opt.dataset.provider===provider&&opt.dataset.model===model);
        });
    }

    const setState=(kind,label)=>{core.className=`core ${kind||''}`;state.textContent=label};
    const setAction=text=>lastAction.textContent=text||'Ready for your next command.';
    const updateMemory=count=>document.querySelector('#memory-count').textContent=`MEMORY ${count||0}`;
    const normalizeHeard=s=>(s||'').toLowerCase().replace(/[^a-z0-9\s]/g,' ').replace(/\s+/g,' ').trim();
    function micReady(){
        return continuousMode && !listening && !requestInFlight && !isSpeaking && Date.now()>=micMutedUntil && recognition;
    }
    function isOwnEcho(phrase){
        const heard=normalizeHeard(phrase);
        if(!heard) return true;
        const spoken=normalizeHeard(lastSpoken);
        if(!spoken) return false;
        if(spoken.includes(heard) || (heard.length>8 && heard.includes(spoken))) return true;
        const tokens=spoken.split(' ').filter(w=>w.length>2);
        if(!tokens.length) return false;
        const bag=new Set(heard.split(' '));
        const hits=tokens.filter(w=>bag.has(w)).length;
        return hits/tokens.length>=0.65 && heard.length<=spoken.length+16;
    }
    function resetTranscript(){
        if(settleTimer){ clearTimeout(settleTimer); settleTimer=null; }
        accumulatedTranscript=''; interimText='';
    }
    function armSettleTimer(){
        if(settleTimer) clearTimeout(settleTimer);
        settleTimer=setTimeout(finalizeSpeech,850);
    }
    function finalizeSpeech(){
        settleTimer=null;
        const phrase=(accumulatedTranscript+' '+interimText).replace(/\s+/g,' ').trim();
        accumulatedTranscript=''; interimText='';
        if(!phrase || !continuousMode || requestInFlight || isSpeaking || Date.now()<micMutedUntil) return;
        if(isOwnEcho(phrase)) return;
        if(phrase.replace(/\s/g,'').length<3) return;
        setAction(`Heard: ${phrase}`);
        requestInFlight=true;
        abortListening();
        executeVoice(phrase);
    }

    function updateStats(data){
        for(const [name,value] of Object.entries({cpu:data.cpu_percent,ram:data.ram_percent,disk:data.disk_percent,battery:data.battery_percent})){
            const label=document.querySelector(`#${name}-value`),bar=document.querySelector(`#${name}-meter`),card=document.querySelector(`#m-${name}`);
            if(label) label.textContent=value==null?'N/A':`${value}%`;
            if(bar) bar.style.width=value==null?'0%':`${value}%`;
            if(card) card.classList.toggle('hot',value!=null&&value>=85);
        }
    }

    function popupFeatures(w,h){
        const left=Math.max(0,(window.innerWidth/2)-(w/2)+window.screenLeft);
        const top=Math.max(0,(window.innerHeight/2)-(w/2)+window.screenTop);
        return `width=${w},height=${h},top=${Math.round(top)},left=${Math.round(left)}`;
    }

    function connectGoogle() {
        setAction('Opening Google authorization...');
        const popup=window.open('about:blank','GoogleAuth',popupFeatures(520,650));
        if(!popup){
            setAction('Popup blocked. Allow popups for this site or press the button again.');
            return;
        }
        const fail=(msg)=>{ try{popup.close();}catch(e){} setAction(`Connection failed: ${msg}`); };
        const timer=setTimeout(()=>fail('timed out reaching the local core.'),8000);
        fetch('/api/auth/google/url',{cache:'no-store'})
            .then(r=>r.json())
            .then(data=>{
                clearTimeout(timer);
                if(!data.url) throw new Error(data.error||'No authorization URL returned.');
                popup.location.href=data.url;
            })
            .catch(err=>{ clearTimeout(timer); fail(err.message); });
    }

    async function disconnectGoogle() {
        if (!confirm('Disconnect Google Workspace?')) return;
        setAction('Disconnecting Google Workspace...');
        try {
            await fetch('/api/auth/google/disconnect', { method: 'POST', headers });
            setAction('Google Workspace disconnected.');
            refreshStatus();
        } catch (err) {
            setAction(`Disconnect failed: ${err.message}`);
        }
    }

    function connectConnector(id) {
        setAction(`Opening ${id} authorization...`);
        const popup=window.open('about:blank',`${id}Auth`,popupFeatures(560,680));
        if(!popup){
            setAction('Popup blocked. Allow popups for this site or press the button again.');
            return;
        }
        const fail=(msg)=>{ try{popup.close();}catch(e){} setAction(`Connection failed: ${msg}`); };
        const timer=setTimeout(()=>fail('timed out reaching the local core.'),8000);
        fetch(`/api/auth/${id}/url`,{cache:'no-store'})
            .then(r=>r.json())
            .then(data=>{
                clearTimeout(timer);
                if(!data.url) throw new Error(data.error||'No authorization URL returned.');
                popup.location.href=data.url;
            })
            .catch(err=>{ clearTimeout(timer); fail(err.message); });
    }

    async function disconnectConnector(id) {
        if (!confirm(`Disconnect ${id}?`)) return;
        setAction(`Disconnecting ${id}...`);
        try {
            await fetch(`/api/auth/${id}/disconnect`, { method: 'POST', headers });
            setAction(`${id} disconnected.`);
            refreshStatus();
        } catch (err) {
            setAction(`Disconnect failed: ${err.message}`);
        }
    }

    async function testConnector(id) {
        setAction(`Testing ${id} connection...`);
        try {
            const r=await fetch(`/api/connectors/test/${id}`, { cache:'no-store' });
            const data=await r.json();
            setAction(data.message || `${id}: test ${data.ok ? 'passed' : 'failed'}.`);
            refreshStatus();
        } catch (err) {
            setAction(`Test failed: ${err.message}`);
        }
    }

    window.addEventListener('message', event => {
        if (event.data && event.data.type === 'jarvis_google_connected') {
            refreshStatus();
            setAction(`Google Workspace connected (${event.data.email || ''}).`);
            speak('Google Workspace is now connected.');
        }
        if (event.data && event.data.type === 'jarvis_connector_connected') {
            refreshStatus();
            setAction(`${event.data.provider || 'Connector'} connected (${event.data.account || ''}).`);
            speak(`${event.data.provider || 'Connector'} is now connected.`);
        }
    });

    function updateConnectors(items){
        const target=document.querySelector('#connectors');
        target.replaceChildren(...items.map(item=>{
            const row=document.createElement('div');
            row.className='connector';
            const dot=document.createElement('i');
            const text=document.createElement('div');
            text.style.flex='1';
            const name=document.createElement('b');
            name.textContent=item.name;
            text.append(name);

            if (item.connected || item.status === 'connected') {
                dot.classList.add('ready');
                const acc=document.createElement('span');
                acc.className='connector-acc';
                acc.textContent=item.account_email ? `✓ ${item.account_email}` : (item.account ? `✓ ${item.account}` : '✓ Connected');
                const btns=document.createElement('div');
                btns.style.display='flex';
                btns.style.gap='6px';
                if (item.id !== 'google') {
                    const testBtn=document.createElement('button');
                    testBtn.className='connector-btn';
                    testBtn.textContent='TEST';
                    testBtn.onclick=()=>testConnector(item.id);
                    btns.append(testBtn);
                }
                const discBtn=document.createElement('button');
                discBtn.className='connector-btn disconnect';
                discBtn.textContent='DISCONNECT';
                discBtn.onclick=item.id==='google'?disconnectGoogle:()=>disconnectConnector(item.id);
                btns.append(discBtn);
                text.append(acc, btns);
            } else if (item.status === 'ready_to_authorize') {
                const detail=document.createElement('small');
                detail.textContent='Credentials configured';
                text.append(detail);
                const connBtn=document.createElement('button');
                connBtn.className='connector-btn';
                connBtn.textContent='CONNECT';
                connBtn.onclick=item.id==='google'?connectGoogle:()=>connectConnector(item.id);
                text.append(connBtn);
            } else {
                const detail=document.createElement('small');
                detail.textContent='Credentials required in .env or via chat';
                text.append(detail);
            }

            row.append(dot,text);
            return row;
        }));
    }

    function updateTasks(items){
        const target=document.querySelector('#tasks');
        if(!items.length){
            target.innerHTML='<small style="color:var(--muted)">Your focus queue is clear.</small>';
            return;
        }
        target.replaceChildren(...items.slice(0,5).map(item=>{
            const row=document.createElement('div');
            row.className='task';
            const dot=document.createElement('i');
            const text=document.createElement('div');
            const name=document.createElement('b');
            name.textContent=item.content;
            const detail=document.createElement('small');
            detail.textContent=`Priority ${item.priority||item.importance||2}`;
            text.append(name,detail);
            row.append(dot,text);
            return row;
        }));
    }

    function updateSkills(items){
        const target=document.querySelector('#skills');
        if(!items?.length){
            target.innerHTML='<small style="color:var(--muted)">No skills installed. Add JSON files to the skills folder.</small>';
            return;
        }
        target.replaceChildren(...items.map(item=>{
            const row=document.createElement('div');
            row.className='connector';
            const dot=document.createElement('i');
            if(item.enabled)dot.classList.add('ready');
            const text=document.createElement('div');
            const name=document.createElement('b');
            name.textContent=item.name;
            const detail=document.createElement('small');
            detail.textContent=item.description||'Skill';
            text.append(name,detail);
            row.append(dot,text);
            row.title=item.enabled?'Active skill. Click to disable.':'Disabled skill. Click to enable.';
            row.style.cursor='pointer';
            row.onclick=async()=>{
                try{
                    const r=await fetch('/api/skills/toggle',{method:'POST',headers,body:JSON.stringify({name:item.name,enabled:!item.enabled})});
                    if(!r.ok)throw new Error((await r.json()).message||'toggle failed');
                    const data=await r.json();
                    setAction(`${item.name} ${data.enabled?'enabled':'disabled'}.`);
                    refreshStatus();
                }catch(e){
                    setAction(`Could not toggle ${item.name}: ${e.message}`);
                }
            };
            return row;
        }));
    }

    async function runAgent(agent){
        const task=window.prompt(`Run ${agent.name} — describe the task:`,`${agent.triggers?.[0]||''} …`);
        if(task===null||!task.trim())return;
        appendChatMessage('user',`[${agent.name}] ${task}`);
        showTyping();
        setAction(`${agent.name} is working…`);
        setState('thinking','AGENT WORKING');
        try{
            const res=await fetch('/api/agents/spawn',{method:'POST',headers,body:JSON.stringify({agent_id:agent.id,task:task.trim(),autonomy:'medium'})});
            const data=await res.json();
            hideTyping();
            if(!res.ok)throw new Error(data.detail||data.error||'spawn failed');
            if(data.result!==undefined){
                const text=typeof data.result==='string'?data.result:JSON.stringify(data.result,null,2);
                appendChatMessage('assistant',text);
                setAction('Agent run completed.');
            }else if(data.error){
                throw new Error(data.error);
            }
            setState('ready','READY');
        }catch(err){
            hideTyping();
            appendChatMessage('assistant','Agent error: '+err.message,true);
            setAction('Agent error: '+err.message);
            setState('error','ERROR');
        }
    }

    async function showAgentRuns(agent){
        try{
            const res=await fetch(`/api/agents/${encodeURIComponent(agent.id)}`,{headers});
            const data=await res.json();
            if(!res.ok)throw new Error(data.error||'status failed');
            const runs=(data.runs||[]).slice(-3);
            if(!runs.length){
                setAction(`${agent.name} has no runs yet. Use RUN to dispatch its first task.`);
                return;
            }
            appendChatMessage('assistant',`${agent.name} — latest runs:\n`+runs.map(r=>`• ${r.status}: ${r.task.slice(0,90)}${r.result?`\n  → ${String(r.result).slice(0,220)}`:''}`).join('\n'));
            openChat();
        }catch(err){
            setAction('Agent status error: '+err.message);
        }
    }

    function agentFaceSVG(id){
        // Cute little agent faces as inline SVG
        const faces={
            lead_hunter:`<svg viewBox="0 0 44 44" fill="none"><circle cx="22" cy="22" r="21" stroke="currentColor" stroke-width="1.5" opacity=".2"/><ellipse cx="22" cy="18" rx="6" ry="5" fill="#2b1604"/><ellipse cx="22" cy="28" rx="3" ry="2" fill="#2b1604" opacity=".6"/><path d="M14 30 Q22 36 30 30" stroke="#2b1604" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>`,
            calendar_commander:`<svg viewBox="0 0 44 44" fill="none"><rect x="6" y="8" width="32" height="28" rx="4" stroke="currentColor" stroke-width="1.5" opacity=".2"/><rect x="10" y="12" width="10" height="6" rx="1.5" fill="#03212e"/><rect x="24" y="12" width="10" height="6" rx="1.5" fill="#03212e"/><rect x="10" y="22" width="24" height="2" rx="1" fill="#03212e" opacity=".5"/></svg>`,
            inbox_zero:`<svg viewBox="0 0 44 44" fill="none"><ellipse cx="22" cy="19" rx="12" ry="10" stroke="currentColor" stroke-width="1.5" opacity=".2"/><path d="M12 19 Q22 12 32 19" stroke="#03291f" stroke-width="2" fill="none" stroke-linecap="round"/><path d="M14 19 Q22 24 30 19" stroke="#03291f" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>`,
            invoice_ivy:`<svg viewBox="0 0 44 44" fill="none"><circle cx="22" cy="22" r="21" stroke="currentColor" stroke-width="1.5" opacity=".2"/><path d="M14 18h16M14 22h16M14 26h10" stroke="#2b1604" stroke-width="1.8" stroke-linecap="round"/><circle cx="34" cy="14" r="5" fill="#ff9ec7" opacity=".8"/></svg>`,
            recruiter_ryan:`<svg viewBox="0 0 44 44" fill="none"><rect x="8" y="6" width="28" height="32" rx="6" stroke="currentColor" stroke-width="1.5" opacity=".2"/><circle cx="22" cy="16" r="5" fill="#0b0620"/><ellipse cx="22" cy="30" rx="10" ry="6" fill="#0b0620" opacity=".6"/></svg>`
        };
        return faces[id]||faces.lead_hunter;
    }

    function updateAgents(items){
        const target=document.querySelector('#agents');
        if(!items?.length){
            target.innerHTML='<small style="color:var(--muted)">Agent hub offline.</small>';
            return;
        }
        latestAgents=items;
        target.replaceChildren(...items.map(agent=>{
            const row=document.createElement('div');
            row.className='agent';
            row.dataset.agentId=agent.id;
            if(activeRuns.has(agent.id)) row.classList.add('running');
            const faceWrap=document.createElement('div');
            faceWrap.className='face-wrap';
            const face=document.createElement('div');
            face.className='agent-face';
            face.dataset.id=agent.id;
            face.innerHTML=agentFaceSVG(agent.id);
            const dot=document.createElement('i');
            dot.className='status-dot';
            faceWrap.append(face,dot);
            const text=document.createElement('div');
            text.style.flex='1';
            const name=document.createElement('b');
            name.textContent=agent.name;
            name.title=agent.persona||'';
            const meta=document.createElement('small');
            const runCount=(agent.runs||[]).length;
            const count=document.createElement('span');
            count.className='run-badge'+(activeRuns.has(agent.id)?' live':'');
            count.textContent=`${runCount} run${runCount===1?'':'s'}`;
            meta.textContent=`${agent.id} · ${agent.autonomy||'medium'}`;
            meta.appendChild(count);
            const run=document.createElement('button');
            run.className='agent-btn';
            run.textContent=activeRuns.has(agent.id)?'WORKING':'RUN';
            if(activeRuns.has(agent.id)) run.classList.add('running');
            run.onclick=e=>{e.stopPropagation();runAgent(agent);};
            const scan=document.createElement('span');
            scan.className='agent-scan';
            row.onclick=()=>showAgentRuns(agent);
            row.title='Click to view recent runs.';
            text.append(name,meta);
            row.append(faceWrap,text,run,scan);
            return row;
        }));
    }

    const sessionRuns=new Map();
    const activeRuns=new Set();
    let latestAgents=[];

    function tryRouteAgentTask(text){
        if(!/^(run|use|deploy|launch|activate|start|ask|connect|tell)\b/i.test(text))return null;
        const low=text.toLowerCase();
        const parts=text.toLowerCase().split(/\b/).map(t=>t.trim().toLowerCase());
        const matches=(latestAgents||[]).filter(a=>{
            const names=[a.id,a.name.toLowerCase(),...(a.triggers||[]).map(t=>String(t).toLowerCase())];
            const tokens=names.filter(n=>n&&n.trim().length>2);
            return tokens.some(t=>parts.includes(t)||low.includes(t));
        });
        if(matches.length)return {agent:matches[0]};
        return null;
    }

    async function runAgent(agent,taskOverride=null){
        let task=taskOverride||window.prompt(`Run ${agent.name} — describe the task:`,`${agent.triggers?.[0]||''} …`);
        if(task===null||!task.trim())return;
        if(!taskOverride)appendChatMessage('user',`[${agent.name}] ${task}`);
        showTyping();
        setAction(`${agent.name} is connecting…`);
        setState('thinking','AGENT WORKING');
        let runId;
        try{
            const res=await fetch('/api/agents/run',{method:'POST',headers,body:JSON.stringify({agent_id:agent.id,task:task.trim(),autonomy:'medium'})});
            const data=await res.json();
            if(!res.ok)throw new Error(data.detail||data.error||'run failed');
            if(data.error)throw new Error(data.error);
            if(data.run_id){runId=data.run_id;sessionRuns.set(runId,{agent,task});}
            hideTyping();
        }catch(err){
            hideTyping();
            appendChatMessage('assistant','Agent error: '+err.message,true);
            setAction('Agent error: '+err.message);
            setState('error','ERROR');
            return;
        }
        const logBox=document.createElement('div');
        logBox.className='agent-run';
        const title=document.createElement('div');
        title.className='run-title';
        title.textContent=`▸ AGENT ${agent.name.toUpperCase()} // ${agent.id}`;
        const track=document.createElement('div');
        track.className='run-track';
        const pill=(label,st)=>`<span class="run-step ${st}"><i></i>${label}</span>`;
        track.innerHTML=pill('LINKED','done')+pill('RUNNING','on')+pill('COMPLETE','');
        const log=document.createElement('div');
        log.className='log-line';
        log.textContent='dispatching run '+runId+' …';
        logBox.append(title,track,log);
        appendChatMessage('assistant',null,false,logBox);
        openChat();
        activeRuns.add(agent.id);
        const card=document.querySelector(`.agent[data-agent-id="${agent.id}"]`);
        if(card)card.classList.add('running');
        const btn=card?.querySelector('.agent-btn');
        if(btn){btn.textContent='WORKING';btn.classList.add('running');}
        setAction(`${agent.name} is working…`);
        const deadline=Date.now()+1000*60*10;
        let final=null;
        while(Date.now()<deadline){
            await new Promise(r=>setTimeout(r,2000));  // slower poll = less load
            let data;
            try{
                const r=await fetch(`/api/agents/${encodeURIComponent(agent.id)}`,{headers});
                data=await r.json();
                if(!r.ok)throw new Error(data.error||'status failed');
            }catch(e){
                log.textContent='status poll error: '+e.message;
                log.className='log-line err';
                continue;
            }
            const run=(data.runs||[]).find(x=>x.id===runId);
            if(!run)continue;
            if(run.status==='pending'||run.status==='running'||run.status==='thinking'){
                track.innerHTML=pill('LINKED','done')+pill('RUNNING','on')+pill('COMPLETE','');
                log.textContent='working — '+run.started_at+' …';
                log.className='log-line';
                continue;
            }
            final=run;
            break;
        }
        activeRuns.delete(agent.id);
        if(card){
            card.classList.remove('running');
            if(btn){btn.textContent='RUN';btn.classList.remove('running');}
        }
        const countPill=card?.querySelector('.run-badge');
        if(countPill){countPill.textContent=`${parseInt(countPill.textContent||'0')+1} runs`;countPill.classList.remove('live');}
        const ok=final&&(final.status==='completed'||(final.result&&!final.error));
        if(ok){
            track.innerHTML=pill('LINKED','done')+pill('RUNNING','done')+pill('COMPLETE','done');
            log.textContent='completed '+new Date(final.completed_at||Date.now()).toLocaleTimeString();
            log.className='log-line ok';
            const text=typeof final.result==='string'?String(final.result).slice(0,4000):JSON.stringify(final.result,null,2).slice(0,4000);
            appendChatMessage('agent',text);
            setAction(`${agent.name} completed.`);
            setState('ready','READY');
        }else{
            track.innerHTML=pill('LINKED','done')+pill('RUNNING','done')+pill('COMPLETE','fail');
            log.textContent='failed: '+(final?.error||'no result / timed out');
            log.className='log-line err';
            appendChatMessage('agent',`${agent.name} failed: ${final?.error||'no result / timed out'}`,true);
            setAction(`${agent.name} failed.`);
            setState('error','ERROR');
        }
    }

    function preferredVoice(){
        if(!('speechSynthesis' in window)) return null;
        const voices=speechSynthesis.getVoices();
        return voices.find(v=>/david|mark|guy|george|male|microsoft.*david/i.test(v.name))||voices.find(v=>v.lang.startsWith('en'))||null;
    }

    function abortListening(){
        if(recognition){
            try{ recognition.abort(); }catch(e){}
        }
        listening=false;
        resetTranscript();
    }

    function finishSpeech(){
        clearTimeout(speechTimer);
        speechTimer=null;
        currentUtterance=null;
        isSpeaking=false;
        resetTranscript();
        micMutedUntil=Date.now()+450;
        setState('','VOICE READY');
        setTimeout(()=>{ if(micReady()) startListening(); }, 480);
    }

    function browserSpeechFallback(text){
        if(!('speechSynthesis' in window)){
            finishSpeech();
            return;
        }
        try{
            speechSynthesis.cancel();
            const utterance=new SpeechSynthesisUtterance(text);
            currentUtterance=utterance;
            utterance.voice=preferredVoice();
            utterance.rate=1.12;
            utterance.pitch=0.95;
            utterance.volume=1;
            utterance.onstart=()=>{
                isSpeaking=true;
                abortListening();
                setState('responding','JARVIS SPEAKING');
            };
            utterance.onend=finishSpeech;
            utterance.onerror=finishSpeech;
            speechSynthesis.speak(utterance);
            const estTime=Math.max(1600, text.length*70)+400;
            speechTimer=setTimeout(finishSpeech, estTime);
        }catch(e){
            finishSpeech();
        }
    }

    async function speak(text){
        lastSpoken=text||'';
        if(!text){ finishSpeech(); return; }
        abortListening();
        isSpeaking=true;
        micMutedUntil=Date.now()+120000;
        setState('responding','JARVIS SPEAKING');
        try{
            const res=await fetch('/api/speak',{method:'POST',headers,body:JSON.stringify({text})});
            const data=await res.json();
            if(!res.ok || !data.available){
                browserSpeechFallback(text);
                return;
            }
            finishSpeech();
        }catch(e){
            browserSpeechFallback(text);
        }
    }

    function showApproval(item){
        pendingApproval=item;
        confirmation.textContent=`CONFIRMATION NEEDED: ${item.description}. Say “confirm” or “cancel”.`;
        confirmation.classList.remove('visible');
        void confirmation.offsetWidth; /* restart animation */
        confirmation.classList.add('visible');
        setState('thinking','AWAITING CONFIRMATION');
        setAction(item.description);
        speak('Confirmation required. Say confirm or cancel.');
    }

    async function resolveApproval(approved){
        if(!pendingApproval)return;
        const action=pendingApproval;
        pendingApproval=null;
        confirmation.classList.remove('visible');
        requestInFlight=true;
        setState('thinking','EXECUTING');
        try{
            const response=await fetch('/api/actions/confirm',{method:'POST',headers,body:JSON.stringify({id:action.id,approved})});
            const data=await response.json();
            if(!response.ok)throw new Error(data.message||'Action failed');
            setAction(data.reply||'Action complete.');
            speak(data.reply||'Action complete.');
        }catch(error){
            setAction(`Action error: ${error.message}`);
            setState('error','ACTION ERROR');
            speak('Action failed.');
        }finally{
            requestInFlight=false;
        }
    }

    async function executeVoice(text){
        const spoken=text.trim();
        if(!spoken){
            requestInFlight=false;
            startListening();
            return;
        }
        const normalized=spoken.toLowerCase();
        if(pendingApproval){
            if(/\b(confirm|yes|do it|proceed)\b/.test(normalized)){
                await resolveApproval(true);
                return;
            }
            if(/\b(cancel|no|stop)\b/.test(normalized)){
                await resolveApproval(false);
                return;
            }
            showApproval(pendingApproval);
            requestInFlight=false;
            return;
        }
        if(/\b(stop listening|go silent|pause listening)\b/.test(normalized)){
            continuousMode=false;
            requestInFlight=false;
            voiceControl.classList.remove('active');
            voiceControl.textContent='ACTIVATE VOICE';
            setAction('Voice mode paused.');
            setState('','VOICE STANDBY');
            abortListening();
            if('speechSynthesis'in window)speechSynthesis.cancel();
            return;
        }
        setAction('Working…');
        setState('thinking','WORKING');
        try{
            const response=await fetch('/api/command',{method:'POST',headers,body:JSON.stringify({text:spoken})});
            const data=await response.json();
            if(!response.ok)throw new Error(data.message||data.detail||'Local core request failed');
            if(data.stats)updateStats(data.stats);
            if(data.tasks)updateTasks(data.tasks);
            if(data.skills)updateSkills(data.skills);
            if(data.memory_count!==undefined)updateMemory(data.memory_count);
            if(data.open_chat)openChat();
            if(data.close_chat)closeChat();
            if(data.confirmations?.length){
                requestInFlight=false;
                showApproval(data.confirmations[0]);
                return;
            }
            // JARVIS controls the chat: long answers live here, spoken part is short.
            const shown=data.chat_text||data.reply||'Completed.';
            if(!data.close_chat)appendChatMessage('assistant',shown);
            if(data.chat_text)openChat();
            setAction(data.reply||'Completed.');
            requestInFlight=false;
            speak(data.reply||'Completed.');
        }catch(error){
            requestInFlight=false;
            setAction(`Core error: ${error.message}`);
            setState('error','CORE ERROR');
            speak('The local core had an error.');
        }
    }

    function startListening(){
        if(!micReady()) return;
        try{
            recognition.start();
        }catch(error){}
    }

    async function armMicrophone(){
        if(!navigator.mediaDevices?.getUserMedia) return;
        try{
            const stream=await navigator.mediaDevices.getUserMedia({
                audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1}
            });
            stream.getTracks().forEach(track=>track.stop());
        }catch(e){
            setAction('Microphone permission is required. Allow it in the browser, then activate again.');
            setState('error','MICROPHONE BLOCKED');
            throw e;
        }
    }

    function releaseMicrophone(){
        if(micStream){
            micStream.getTracks().forEach(track=>track.stop());
            micStream=null;
        }
    }

    let statusInFlight=false;
    async function refreshStatus(){
        if(statusInFlight || document.hidden || requestInFlight) return;
        statusInFlight=true;
        try{
            const [healthResponse,statsResponse,tasksResponse]=await Promise.all([fetch('/api/health'),fetch('/api/stats'),fetch('/api/tasks')]);
            const health=await healthResponse.json();
            const dot=document.querySelector('#health-dot'),label=document.querySelector('#health-text');
            if(healthResponse.ok&&health.status==='ok'){
                dot.classList.add('online');
                label.textContent='LOCAL CORE ONLINE';
                updateConnectors(health.connectors||[]);
                updateSkills(health.skills||[]);
                updateAgents(health.agents||[]);
                updateMemory(health.memory_count);
            }else{
                dot.classList.remove('online');
                label.textContent='LOCAL CORE OFFLINE';
            }
            if(statsResponse.ok)updateStats(await statsResponse.json());
            if(tasksResponse.ok)updateTasks((await tasksResponse.json()).tasks||[]);
        }catch{
            document.querySelector('#health-dot').classList.remove('online');
            document.querySelector('#health-text').textContent='LOCAL CORE OFFLINE';
        }finally{
            statusInFlight=false;
        }
    }

    const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
    if(Recognition){
        recognition=new Recognition();
        recognition.lang='en-US';
        recognition.interimResults=true;
        recognition.continuous=true;
        recognition.maxAlternatives=3;

        recognition.onstart=()=>{
            listening=true;
            resetTranscript();
            if(!isSpeaking) setState('listening','LISTENING');
        };

        recognition.onresult=event=>{
            if(isSpeaking || requestInFlight || Date.now()<micMutedUntil){
                resetTranscript();
                return;
            }
            for(let i=event.resultIndex;i<event.results.length;i++){
                const item=event.results[i];
                const alt=item[0]||{};
                const chunk=(alt.transcript||'').trim();
                if(!chunk) continue;
                if(item.isFinal){
                    accumulatedTranscript=(accumulatedTranscript+' '+chunk).trim();
                    interimText='';
                }else{
                    interimText=chunk;
                }
            }
            const displayed=(accumulatedTranscript+' '+interimText).replace(/\s+/g,' ').trim();
            if(displayed && !isOwnEcho(displayed)) setAction(`Listening: ${displayed}`);
            if(accumulatedTranscript && /[.!?]$/.test(accumulatedTranscript.trim())){
                finalizeSpeech();
                return;
            }
            armSettleTimer();
        };

        recognition.onerror=event=>{
            listening=false;
            if(['aborted','no-speech'].includes(event.error)){
                if(continuousMode && !requestInFlight && !isSpeaking && Date.now()>=micMutedUntil){
                    setState('','VOICE READY');
                }
                return;
            }
            if(['not-allowed','service-not-allowed'].includes(event.error)){
                continuousMode=false;
                voiceControl.classList.remove('active');
                voiceControl.textContent='ACTIVATE VOICE';
                releaseMicrophone();
                setAction('Microphone permission is blocked. Allow it in your browser settings.');
                setState('error','MICROPHONE BLOCKED');
                return;
            }
            setAction(`Voice error: ${event.error}. Retrying...`);
            setState('error','VOICE RETRYING');
            setTimeout(startListening, 400);
        };

        recognition.onend=()=>{
            listening=false;
            if(accumulatedTranscript.trim() && !requestInFlight && !isSpeaking && Date.now()>=micMutedUntil){
                finalizeSpeech();
                return;
            }
            if(micReady()) setTimeout(startListening, 80);
        };

        voiceControl.onclick=async()=>{
            continuousMode=!continuousMode;
            voiceControl.classList.toggle('active',continuousMode);
            voiceControl.textContent=continuousMode?'VOICE ACTIVE':'ACTIVATE VOICE';
            if(continuousMode){
                try{
                    await armMicrophone();
                }catch(e){
                    continuousMode=false;
                    voiceControl.classList.remove('active');
                    voiceControl.textContent='ACTIVATE VOICE';
                    return;
                }
                setAction('Listening. Speak a command.');
                setState('','VOICE READY');
                startListening();
                if(watchdog) clearInterval(watchdog);
                watchdog=setInterval(()=>{ if(micReady()) startListening(); }, 1500);
            }else{
                if(watchdog){ clearInterval(watchdog); watchdog=null; }
                abortListening();
                releaseMicrophone();
                setAction('Voice mode paused.');
                setState('','VOICE STANDBY');
            }
        };
    }else{
        voiceControl.disabled=true;
        voiceControl.textContent='VOICE NOT SUPPORTED';
        setAction('Use Chrome or Edge for browser speech recognition.');
    }

    if('speechSynthesis'in window) speechSynthesis.onvoiceschanged=preferredVoice;
    refreshStatus();
    setInterval(refreshStatus,5000);
    try{ initModelSelector(); }catch(e){}
    try{ // mirror voice commands into chat
        const originalExecuteVoice=executeVoice;
        executeVoice=async function(text){
            if(!chatOpen&&text)appendChatMessage('user',text);
            await originalExecuteVoice(text);
        };
    }catch(e){}
</script>
</body>
</html>
