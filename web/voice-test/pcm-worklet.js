// Doorstep voice: microphone capture and playback as 16 kHz PCM16, in an AudioWorklet.
//
// The page asks for an AudioContext at 16 kHz, so the browser resamples the microphone and the
// speaker itself (the recommended path). If the browser gives another rate, both processors
// convert with linear interpolation, which is enough for speech.
//
// Capture posts 20 ms Int16 frames (320 samples). Playback queues Int16 chunks and drops the
// whole queue on 'clear' (barge-in: the resident started talking over the agent).

const RATE = 16000;
const FRAME = 320;

class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / RATE; // input samples per output sample
    this.pos = 0;
    this.frame = new Int16Array(FRAME);
    this.n = 0;
  }

  push(x) {
    const s = Math.max(-1, Math.min(1, x));
    this.frame[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this.n === FRAME) {
      this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
      this.frame = new Int16Array(FRAME);
      this.n = 0;
    }
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    if (this.step === 1) {
      for (let i = 0; i < ch.length; i++) this.push(ch[i]);
      return true;
    }
    while (this.pos < ch.length) {
      const i = Math.floor(this.pos);
      const f = this.pos - i;
      const a = ch[i];
      const b = i + 1 < ch.length ? ch[i + 1] : a;
      this.push(a + (b - a) * f);
      this.pos += this.step;
    }
    this.pos -= ch.length;
    return true;
  }
}

class Player extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = RATE / sampleRate; // input samples per output sample
    this.queue = [];
    this.cur = null;
    this.idx = 0;
    this.frac = 0;
    this.last = 0;
    this.port.onmessage = (e) => {
      if (e.data === "clear") {
        this.queue = [];
        this.cur = null;
        this.idx = 0;
      } else {
        this.queue.push(new Int16Array(e.data));
      }
    };
  }

  sample() {
    while (!this.cur || this.idx >= this.cur.length) {
      if (!this.queue.length) return null;
      this.cur = this.queue.shift();
      this.idx = 0;
    }
    return this.cur[this.idx] / 0x8000;
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    for (let i = 0; i < out.length; i++) {
      const s = this.sample();
      if (s === null) {
        out[i] = 0;
        continue;
      }
      out[i] = s;
      this.frac += this.step;
      while (this.frac >= 1) {
        this.frac -= 1;
        this.idx++;
      }
    }
    return true;
  }
}

registerProcessor("doorstep-capture", Capture);
registerProcessor("doorstep-player", Player);
