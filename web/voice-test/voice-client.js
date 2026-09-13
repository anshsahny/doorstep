// Doorstep browser voice client. Phase 5's dashboard imports this as-is.
//
//   const call = await startCall({ apiUrl, token, incidentId, residentId, on: {...} });
//   (`token` is the dashboard session: a sandbox drill's, or the captain's.)
//   call.hangUp();
//
// Wire protocol: see voice/doorstep_voice/ports.py.

export async function startCall({ apiUrl, token, incidentId, residentId, on = {} }) {
  const emit = (name, ...args) => on[name] && on[name](...args);

  const res = await fetch(`${apiUrl.replace(/\/$/, "")}/voice/session`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ incident_id: incidentId, resident_id: residentId }),
  });
  const link = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(link.error || `Could not start the call (${res.status})`);

  const mic = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  let ctx;
  try {
    ctx = new AudioContext({ sampleRate: 16000 });
  } catch {
    ctx = new AudioContext();
  }
  await ctx.audioWorklet.addModule(new URL("./pcm-worklet.js", import.meta.url));
  const source = ctx.createMediaStreamSource(mic);
  const capture = new AudioWorkletNode(ctx, "doorstep-capture");
  const player = new AudioWorkletNode(ctx, "doorstep-player", { outputChannelCount: [1] });
  const mute = ctx.createGain();
  mute.gain.value = 0;
  source.connect(capture).connect(mute).connect(ctx.destination);
  player.connect(ctx.destination);
  emit("status", `audio ready at ${ctx.sampleRate} Hz`);

  const ws = new WebSocket(link.url);
  ws.binaryType = "arraybuffer";
  let ended = false;

  const cleanup = () => {
    mic.getTracks().forEach((t) => t.stop());
    ctx.close().catch(() => {});
  };

  capture.port.onmessage = (e) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(e.data);
  };
  let connected = false;
  ws.onopen = () => connected || emit("status", "connecting to Doorstep…");
  ws.onmessage = (e) => {
    if (typeof e.data !== "string") {
      player.port.postMessage(e.data, [e.data]);
      return;
    }
    const msg = JSON.parse(e.data);
    if (msg.type === "clear") player.port.postMessage("clear");
    else if (msg.type === "transcript") {
      if (!connected) {
        connected = true;
        emit("status", "connected");
      }
      emit("transcript", msg.speaker, msg.text);
    } else if (msg.type === "status") {
      connected = connected || msg.status === "connected";
      emit("status", msg.status);
    }
    else if (msg.type === "error") emit("error", msg.message);
    else if (msg.type === "ended") {
      ended = true;
      emit("ended", msg.reason);
    }
  };
  ws.onclose = (e) => {
    if (!ended) emit("ended", e.reason || `connection closed (${e.code})`);
    // Let the last words finish playing before the audio graph goes away.
    setTimeout(cleanup, 1500);
  };
  ws.onerror = () => emit("error", "The voice connection failed.");

  return {
    maxSeconds: link.max_call_seconds,
    hangUp() {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "hangup" }));
      setTimeout(() => ws.close(), 300);
    },
  };
}
