// Eight small level bands from the actual microphone packet; no idle animation.
export class MicrophoneSignal {
  constructor(count = 8) {
    this.levels = Array(count).fill(0);
    this.samples = Array(48).fill(0);
  }

  observe(pcm) {
    const samples = pcm instanceof Int16Array ? pcm : new Int16Array(pcm);
    const count = this.levels.length;
    if (!samples.length) return this.clear();
    for (let band = 0; band < count; band++) {
      const start = Math.floor(band * samples.length / count);
      const end = Math.floor((band + 1) * samples.length / count);
      let sum = 0;
      for (let i = start; i < end; i++) {
        const value = samples[i] / 32768;
        sum += value * value;
      }
      const rms = Math.sqrt(sum / Math.max(1, end - start));
      const target = Math.min(1, Math.max(0, (rms - 0.008) * 7));
      this.levels[band] += (target - this.levels[band]) * (target > this.levels[band] ? 0.68 : 0.4);
    }
    // Display shape and loudness separately: normalization reveals the signal
    // without allowing a single loud sample to make a huge spike.
    let peak=0;for(const sample of samples)peak=Math.max(peak,Math.abs(sample));
    for(let i=0;i<this.samples.length;i++){
      const index=Math.min(samples.length-1,Math.floor(i*samples.length/this.samples.length));
      this.samples[i]=peak>260?samples[index]/peak:0;
    }
    return this.levels;
  }

  clear() {
    this.levels.fill(0);
    this.samples.fill(0);
    return this.levels;
  }
}
