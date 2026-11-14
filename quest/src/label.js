// Canvas-texture text, redrawn only when the text changes.
import * as THREE from 'three';

export class CanvasText {
  constructor(w, h) {
    this.canvas = document.createElement('canvas');
    this.canvas.width = w;
    this.canvas.height = h;
    this.ctx = this.canvas.getContext('2d');
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.key = null;
  }

  // lines: array of strings; colour as a css string
  draw(lines, color = '#00E5FF', bg = 'rgba(0,0,0,0.65)') {
    const key = lines.join('\n') + color;
    if (key === this.key) return;
    this.key = key;
    const { ctx, canvas } = this;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = color;
    ctx.lineWidth = 4;
    ctx.strokeRect(2, 2, canvas.width - 4, canvas.height - 4);
    const lh = Math.floor((canvas.height - 12) / Math.max(lines.length, 1));
    ctx.font = `bold ${Math.floor(lh * 0.78)}px sans-serif`;
    ctx.fillStyle = color;
    ctx.textBaseline = 'middle';
    lines.forEach((s, i) => ctx.fillText(s, 14, 6 + lh * (i + 0.5)));
    this.texture.needsUpdate = true;
  }
}

export const css = (hex) => '#' + hex.toString(16).padStart(6, '0');
