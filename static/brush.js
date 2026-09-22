/* Soft round stroke engine; kept numerically aligned with engine.brush_coverage. */
(function (root) {
  class BrushStroke {
    constructor(width, height, radius, hardness, opacity, flow, spacing = .1) {
      Object.assign(this, {width, height, radius: Math.max(.25, radius), hardness, opacity, flow});
      this.step = Math.max(.25, 2 * this.radius * spacing);
      this.remaining = this.step;
      this.data = new Float32Array(width * height);
      this.last = null;
    }
    add(x, y) {
      if (!this.last) { this.stamp(x, y); this.last = [x, y]; return; }
      const [ax, ay] = this.last, dx = x - ax, dy = y - ay, length = Math.hypot(dx, dy);
      if (!length) return;
      let offset = this.remaining;
      while (offset <= length + 1e-9) {
        this.stamp(ax + dx * offset / length, ay + dy * offset / length);
        offset += this.step;
      }
      this.remaining = offset - length;
      this.last = [x, y];
    }
    stamp(x, y) {
      if (!this.opacity || !this.flow) return;
      const r = this.radius;
      const x0 = Math.max(0, Math.floor(x-r-.5)), y0 = Math.max(0, Math.floor(y-r-.5));
      const x1 = Math.min(this.width, Math.ceil(x+r+.5)+1), y1 = Math.min(this.height, Math.ceil(y+r+.5)+1);
      for (let py=y0; py<y1; py++) for (let px=x0; px<x1; px++) {
        const d=Math.hypot(px-x,py-y), edge=Math.max(0,Math.min(1,r+.5-d));
        const t=Math.max(0,Math.min(1,(r-d)/Math.max(r*(1-this.hardness),.001)));
        const tip=this.hardness>=1 ? edge : t*t*(3-2*t)*edge;
        const index=py*this.width+px, old=this.data[index];
        this.data[index]=this.flow>=1 ? Math.max(old,tip) : old+tip*this.flow*(1-old);
      }
    }
    render(context, color, multiplier=1) {
      const image=context.createImageData(this.width,this.height), out=image.data;
      for (let i=0; i<this.data.length; i++) {
        out[i*4]=color[0];out[i*4+1]=color[1];out[i*4+2]=color[2];
        out[i*4+3]=Math.round(this.data[i]*this.opacity*255*multiplier);
      }
      context.putImageData(image,0,0);
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports=BrushStroke;
  else root.BrushStroke=BrushStroke;
})(typeof window !== 'undefined' ? window : globalThis);
