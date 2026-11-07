// Phone side: camera -> WebRTC -> stream_server.py. Results come back on a data channel.
"use strict";

const $ = (id) => document.getElementById(id);
let pc = null, stream = null, wakeLock = null, statsTimer = null;
let results = 0, resultsAtLastTick = 0;

function setState(text, cls = "") {
  $("state").textContent = text;
  $("state").className = `pill ${cls}`;
}

function showError(msg) {
  $("error").hidden = !msg;
  $("error").textContent = msg || "";
}

function waitForIce(pc) {
  // Send one complete offer (all candidates inside) instead of trickling them.
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const check = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", check);
        resolve();
      }
    };
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(resolve, 3000);           // on a LAN the host candidates are enough
  });
}

async function start() {
  showError("");
  $("start").disabled = true;
  try {
    const [w, h] = $("size").value.split("x").map(Number);
    stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: { ideal: $("facing").value },
        width: { ideal: w }, height: { ideal: h },
        frameRate: { ideal: Number($("fps").value) },
      },
    });
    $("preview").srcObject = stream;

    pc = new RTCPeerConnection();         // same network: no STUN/TURN server needed
    pc.onconnectionstatechange = () => {
      setState(pc.connectionState, pc.connectionState);
      if (pc.connectionState === "failed") {
        showError("Connection failed. Is the phone on the same Wi-Fi as the computer? See README troubleshooting.");
      }
    };
    stream.getTracks().forEach((t) => pc.addTrack(t, stream));
    const dc = pc.createDataChannel("results");
    dc.onmessage = (e) => onResult(JSON.parse(e.data));

    setState("connecting");
    await pc.setLocalDescription(await pc.createOffer());
    await waitForIce(pc);
    const r = await fetch("/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
    });
    if (!r.ok) throw new Error(`server answered ${r.status}`);
    await pc.setRemoteDescription(await r.json());

    $("stop").disabled = false;
    statsTimer = setInterval(showStats, 1000);
    try { wakeLock = await navigator.wakeLock.request("screen"); } catch (e) { /* not supported: fine */ }
  } catch (e) {
    showError(e.name === "NotAllowedError" ? "Camera permission denied: allow it in Chrome's site settings." : String(e));
    await stop();
  }
}

async function stop() {
  clearInterval(statsTimer);
  if (pc) { pc.close(); pc = null; }
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  $("preview").srcObject = null;
  $("start").disabled = false;
  $("stop").disabled = true;
  setState("idle");
}

function onResult(m) {
  results++;
  $("latency").textContent = `${(m.latency_s * 1000).toFixed(0)} ms (model ${(m.infer_s * 1000).toFixed(0)} ms)`;
  $("dropped").textContent = `${m.frames_dropped} of ${m.frames_in} frames`;
  $("result").textContent = JSON.stringify(m.result, null, 1);
}

async function showStats() {
  if (!pc) return;
  const stats = await pc.getStats();
  stats.forEach((s) => {
    if (s.type === "outbound-rtp" && s.kind === "video") {
      const size = s.frameWidth ? `${s.frameWidth}×${s.frameHeight}` : "";
      $("sending").textContent = `${size} @ ${(s.framesPerSecond || 0).toFixed(0)} fps`;
    }
  });
  $("model").textContent = `${results - resultsAtLastTick} results/s`;
  resultsAtLastTick = results;
}

$("start").onclick = start;
$("stop").onclick = stop;
if (!window.isSecureContext || !navigator.mediaDevices) {
  $("insecure").hidden = false;
  $("start").disabled = true;
}
