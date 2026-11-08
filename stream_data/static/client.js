// Phone side: camera -> WebRTC -> stream_server.py. Results come back on a data channel.
"use strict";

const $ = (id) => document.getElementById(id);
let pc = null, stream = null, wakeLock = null, statsTimer = null;
let results = 0, resultsAtLastTick = 0;
let lastBytes = 0, lastBytesT = 0;

// Locked quality: the phone keeps the chosen resolution and bitrate and lets the frame rate drop
// instead (Chrome's default is to shrink the picture and the bitrate to keep the frame rate up).
// Returns null for auto, else {min, start, max} in kbps.
//   fixed N  : start at N and hold it (min = max = N)
//   max      : start high and climb as far as the Wi-Fi carries (up to 100 Mbps); never below 4 Mbps
function lockedRate() {
  const q = $("quality").value;
  if (q === "auto") return null;
  if (q === "max") return { min: 4000, start: 15000, max: 100000 };
  const k = Number(q);
  return { min: k, start: k, max: k };
}

async function openCamera(w, h, fps, exact) {
  const dim = (v) => (exact ? { exact: v } : { ideal: v });
  return navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { facingMode: { ideal: $("facing").value }, width: dim(w), height: dim(h), frameRate: { ideal: fps } },
  });
}

// Chrome honours x-google-{min,start,max}-bitrate in the answer: start straight at the chosen bitrate
// and never go below the minimum, instead of ramping up from ~300 kbps and backing off. b=AS lifts
// the session cap, which otherwise holds 1080p at a few Mbps.
function forceBitrate(sdp, rate) {
  const lines = sdp.split("\r\n").filter((l) => !l.startsWith("b=AS:") && !l.startsWith("b=TIAS:"));
  const out = [];
  let inVideo = false;
  const params = `x-google-min-bitrate=${rate.min};x-google-start-bitrate=${rate.start};x-google-max-bitrate=${rate.max}`;
  const withFmtp = new Set(lines.filter((l) => l.startsWith("a=fmtp:")).map((l) => l.slice(7).split(" ")[0]));
  for (const l of lines) {
    if (l.startsWith("m=")) inVideo = l.startsWith("m=video");
    if (inVideo && l.startsWith("a=fmtp:") && !/apt=/.test(l)) {
      out.push(`${l};${params}`);
      continue;
    }
    out.push(l);
    if (inVideo && l.startsWith("c=")) out.push(`b=AS:${rate.max}`);
    const m = inVideo && l.match(/^a=rtpmap:(\d+) (VP8|VP9|H264|AV1)\//i);
    if (m && !withFmtp.has(m[1])) out.push(`a=fmtp:${m[1]} ${params}`);
  }
  return out.join("\r\n");
}

async function lockSender(sender, rate, fps) {
  const p = sender.getParameters();
  p.degradationPreference = "maintain-resolution";
  if (!p.encodings || !p.encodings.length) p.encodings = [{}];
  p.encodings[0].maxBitrate = rate.max * 1000;
  p.encodings[0].scaleResolutionDownBy = 1;
  p.encodings[0].maxFramerate = fps;
  try { await sender.setParameters(p); } catch (e) { console.warn("setParameters", e); }
}

// Remember the phone's last choices.
for (const id of ["facing", "size", "fps", "quality"]) {
  try {
    const v = localStorage.getItem(`sightline.${id}`);
    if (v && [...$(id).options].some((o) => o.value === v)) $(id).value = v;
    $(id).addEventListener("change", () => { try { localStorage.setItem(`sightline.${id}`, $(id).value); } catch (e) {} });
  } catch (e) { /* storage blocked: defaults are fine */ }
}

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
    const fps = Number($("fps").value), rate = lockedRate();
    try {
      stream = await openCamera(w, h, fps, !!rate);           // locked: insist on the exact size
    } catch (e) {
      if (e.name !== "OverconstrainedError") throw e;
      stream = await openCamera(w, h, fps, false);              // this camera can't do it: closest it can
      showError(`This camera can't do exactly ${w}×${h}; using the closest size it has.`);
    }
    if (rate) stream.getVideoTracks().forEach((t) => { t.contentHint = "detail"; });
    $("preview").srcObject = stream;

    pc = new RTCPeerConnection();         // same network: no STUN/TURN server needed
    pc.onconnectionstatechange = () => {
      setState(pc.connectionState, pc.connectionState);
      if (pc.connectionState === "failed") {
        showError("Connection failed. Is the phone on the same Wi-Fi as the computer? See README troubleshooting.");
      }
    };
    const senders = stream.getTracks().map((t) => pc.addTrack(t, stream));
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
    const answer = await r.json();
    if (rate) answer.sdp = forceBitrate(answer.sdp, rate);
    await pc.setRemoteDescription(answer);
    if (rate) {
      for (const s of senders) if (s.track && s.track.kind === "video") await lockSender(s, rate, fps);
    }

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
  lastBytes = lastBytesT = 0;
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
      let rate = "";
      if (lastBytesT && s.timestamp > lastBytesT) {
        rate = ` · ${((s.bytesSent - lastBytes) * 8 / (s.timestamp - lastBytesT)).toFixed(0)} kbps`;
      }
      lastBytes = s.bytesSent; lastBytesT = s.timestamp;
      $("sending").textContent = `${size} @ ${(s.framesPerSecond || 0).toFixed(0)} fps${rate}`;
      const why = s.qualityLimitationReason;
      $("limited").textContent = !why || why === "none" ? "nothing" :
        why === "cpu" ? "phone CPU (encoder)" : why === "bandwidth" ? "Wi-Fi bandwidth" : why;
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
