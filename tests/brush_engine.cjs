const assert = require('node:assert/strict');
const BrushStroke = require('../static/brush.js');
function stroke(points, opacity=.5, flow=.1, hardness=.5) {
  const result=new BrushStroke(140,100,15,hardness,opacity,flow,.1);
  for (const point of points) result.add(...point);
  return result;
}
if (process.argv.includes('--fixtures')) {
  const data=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
  process.stdout.write(JSON.stringify(data.map(f=>{
    const s=new BrushStroke(f.width,f.height,f.radius,f.hardness,f.opacity,f.flow,f.spacing);
    f.points.forEach(p=>s.add(...p));
    return Array.from(s.data,v=>v*s.opacity);
  })));
} else {
  const one=stroke([[25,50],[110,50]]), repeated=stroke([[25,50],[110,50],[25,50],[110,50]]);
  assert(repeated.data[50*140+60]>one.data[50*140+60]);
  assert(Math.max(...repeated.data)*repeated.opacity<=.5);
  const a=stroke([[25,50],[110,50]],.1,1), b=stroke([[25,50],[110,50]],.6,1);
  assert.equal(a.data[50*140+60]*a.opacity,.1);
  assert.equal(b.data[50*140+60]*b.opacity,.6);
  const dense=stroke(Array.from({length:86},(_,i)=>[25+i,50]));
  for(let i=0;i<one.data.length;i++) assert(Math.abs(one.data[i]-dense.data[i])<1e-6);
  console.log('PASS: same-stroke flow buildup, opacity limit and pointer-density invariance.');
}
