// Canvas-texture text, redrawn only when the content changes.
import * as THREE from 'three';

export const css = (hex) => '#' + hex.toString(16).padStart(6, '0');

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

  // lines of text, dark rounded background, coloured border
  draw(lines, { color = '#00E5FF', bg = 'rgba(0,0,0,0.72)', center = false } = {}) {
    const key = lines.join('\n') + color + center;
    if (key === this.key) return;
    this.key = key;
    const { ctx, canvas } = this;
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = bg;
    ctx.beginPath();
    ctx.roundRect(3, 3, W - 6, H - 6, 14);
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = 4;
    ctx.stroke();
    const lh = Math.floor((H - 12) / Math.max(lines.length, 1));
    ctx.font = `bold ${Math.floor(lh * 0.72)}px sans-serif`;
    ctx.fillStyle = color;
    ctx.textBaseline = 'middle';
    ctx.textAlign = center ? 'center' : 'left';
    lines.forEach((s, i) => ctx.fillText(s, center ? W / 2 : 16, 6 + lh * (i + 0.5), W - 24));
    this.texture.needsUpdate = true;
  }
}

// a camera-facing text sprite of the given world width (m)
export function textSprite(w, h, widthM) {
  const text = new CanvasText(w, h);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: text.texture, transparent: true, depthTest: false }));
  sprite.scale.set(widthM, (widthM * h) / w, 1);
  sprite.renderOrder = 20;
  return { text, sprite };
}
