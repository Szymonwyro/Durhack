// Small head-locked panel low in view for calibration: text on the left, a mini diagram of the
// corners on the right (done = cyan, next = white ring, too far off = orange, pending = grey).
import * as THREE from 'three';

const W = 640, H = 300;

export class CalibPanel {
  constructor(camera) {
    this.canvas = document.createElement('canvas');
    this.canvas.width = W;
    this.canvas.height = H;
    this.ctx = this.canvas.getContext('2d');
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.256, 0.12),
      new THREE.MeshBasicMaterial({ map: this.texture, transparent: true, depthTest: false }));
    this.mesh.position.set(0, -0.19, -0.55);
    this.mesh.rotation.x = 0.3; // tilt towards the eyes
    this.mesh.renderOrder = 40;
    this.mesh.visible = false;
    camera.add(this.mesh);
    this.key = null;
  }

  // info: { visible, title, lines[], warn, points[[x,y]], labels[], done[], active, bad[] }
  update(info) {
    this.mesh.visible = !!info.visible;
    if (!info.visible) return;
    const key = JSON.stringify(info);
    if (key === this.key) return;
    this.key = key;
    this.draw(info);
  }

  draw(info) {
    const { ctx } = this;
    const accent = info.warn ? '#FF8A00' : '#00E5FF';
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = 'rgba(0,0,0,0.78)';
    ctx.beginPath();
    ctx.roundRect(3, 3, W - 6, H - 6, 18);
    ctx.fill();
    ctx.strokeStyle = accent;
    ctx.lineWidth = 4;
    ctx.stroke();

    // text column
    const tx = 22, tw = 400;
    let y = 30;
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';
    ctx.fillStyle = '#FFFFFF';
    ctx.font = 'bold 36px sans-serif';
    for (const l of wrap(ctx, info.title || '', tw)) { ctx.fillText(l, tx, y); y += 42; }
    y += 8;
    ctx.font = 'bold 27px sans-serif';
    for (const line of info.lines || []) {
      ctx.fillStyle = line.startsWith('!') ? '#FF8A00' : accent;
      for (const l of wrap(ctx, line.replace(/^!/, ''), tw)) { ctx.fillText(l, tx, y); y += 32; }
    }

    // corner diagram
    const pts = info.points || [];
    if (pts.length < 2) { this.texture.needsUpdate = true; return; }
    const bx = 450, by = 40, bs = 165;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const s = bs / Math.max(maxX - minX, maxY - minY, 1e-3);
    const P = pts.map(([x, yy]) => [bx + (x - minX) * s + (bs - (maxX - minX) * s) / 2,
      by + bs - (yy - minY) * s - (bs - (maxY - minY) * s) / 2]); // map +y up
    ctx.strokeStyle = 'rgba(255,255,255,0.35)';
    ctx.lineWidth = 3;
    ctx.beginPath();
    P.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
    ctx.closePath();
    ctx.stroke();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = 'bold 24px sans-serif';
    P.forEach(([px, py], i) => {
      const done = info.done?.[i], bad = info.bad?.[i], active = info.active === i;
      ctx.beginPath();
      ctx.arc(px, py, active ? 22 : 18, 0, Math.PI * 2);
      if (bad) { ctx.fillStyle = '#FF8A00'; ctx.fill(); }
      else if (done) { ctx.fillStyle = '#00E5FF'; ctx.fill(); }
      else { ctx.fillStyle = 'rgba(0,0,0,0.9)'; ctx.fill(); }
      ctx.lineWidth = active ? 6 : 3;
      ctx.strokeStyle = active ? '#FFFFFF' : (done || bad) ? 'rgba(0,0,0,0)' : '#888888';
      ctx.stroke();
      ctx.fillStyle = done || bad ? '#000000' : '#FFFFFF';
      ctx.fillText(info.labels?.[i] ?? String(i), px, py + 1);
    });
    this.texture.needsUpdate = true;
  }
}

function wrap(ctx, text, width) {
  const out = [];
  let line = '';
  for (const word of text.split(' ')) {
    const test = line ? line + ' ' + word : word;
    if (ctx.measureText(test).width > width && line) { out.push(line); line = word; } else line = test;
  }
  if (line) out.push(line);
  return out;
}
