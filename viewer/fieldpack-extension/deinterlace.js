// Field-pack viewer deinterlacer (issue #19). Pure WebGL, no dependencies.
// Input: a 1920x1080 frame whose top 1920x540 rows hold one field (even or odd source rows),
// with a 16x16 parity marker in the bottom-left corner. Output: full 1920x1080 picture.
//   marker: pixel (8, H-8) luma > 127  => parity 1 (odd rows), else parity 0 (even rows).
// Missing rows: motion-adaptive. Static area -> previous output row (temporal); moving area -> spatial average.

export const FP_W = 1920, FP_H = 1080, FP_FIELD_H = 540;

const VS = `attribute vec2 p; varying vec2 uv;
void main(){ uv = vec2(p.x*0.5+0.5, 0.5-p.y*0.5); gl_Position = vec4(p,0.,1.); }`;

const FS = `precision highp float;
varying vec2 uv;
uniform sampler2D cur;     // current packed frame
uniform sampler2D prev;    // previous *output* frame (1920x1080)
uniform vec2 res;          // (1920,1080)
uniform float threshold;   // luma difference counted as motion (0..1)

float luma(vec2 px){ return texture2D(cur, (px + 0.5) / res).r; }

void main(){
  vec2 px = floor(uv * res);            // output pixel
  float row = px.y;
  float marker = texture2D(cur, vec2(8.5, res.y - 7.5) / res).r;
  float parity = marker > 0.5 ? 1.0 : 0.0;
  float rowParity = mod(row, 2.0);
  float fieldRow = floor(row / 2.0);    // row index inside the field

  if (rowParity == parity) {
    // Row belongs to the field we received: copy it, field row k sits at packed row k.
    gl_FragColor = texture2D(cur, vec2(px.x + 0.5, fieldRow + 0.5) / res);
    return;
  }
  // Missing row: spatial neighbours from the received field (rows above/below in the output).
  vec2 aboveP = vec2(px.x + 0.5, (row - 1.0 + 0.5));
  vec2 belowP = vec2(px.x + 0.5, (row + 1.0 + 0.5));
  // Packed field row for neighbour rows in the same field grid.
  vec3 above = texture2D(cur, vec2(px.x + 0.5, floor((row - 1.0) / 2.0) + 0.5) / res).rgb;
  vec3 below = texture2D(cur, vec2(px.x + 0.5, floor((row + 1.0) / 2.0) + 0.5) / res).rgb;
  vec3 spatial = (above + below) * 0.5;
  vec3 temporal = texture2D(prev, (px + 0.5) / res).rgb;
  float motion = abs(dot(spatial - temporal, vec3(0.299, 0.587, 0.114)));
  gl_FragColor = vec4(motion > threshold ? spatial : temporal, 1.0);
}`;

function compile(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
  return s;
}

// Creates a deinterlacer bound to a canvas. draw(src) renders one output frame to the canvas.
export function createDeinterlacer(canvas, threshold = 0.05) {
  const gl = canvas.getContext("webgl", { premultipliedAlpha: false, preserveDrawingBuffer: true });
  if (!gl) throw new Error("WebGL unavailable");
  canvas.width = FP_W; canvas.height = FP_H;
  const prog = gl.createProgram();
  gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VS));
  gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FS));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
  gl.useProgram(prog);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "p");
  gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

  const mk = () => {
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, FP_W, FP_H, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    return t;
  };
  const curTex = mk(), prevTex = mk();

  const uCur = gl.getUniformLocation(prog, "cur");
  const uPrev = gl.getUniformLocation(prog, "prev");
  gl.uniform1i(uCur, 0); gl.uniform1i(uPrev, 1);
  gl.uniform2f(gl.getUniformLocation(prog, "res"), FP_W, FP_H);
  gl.uniform1f(gl.getUniformLocation(prog, "threshold"), threshold);

  return {
    // src: <video>, <img> or <canvas> holding the packed 1920x1080 frame.
    draw(src) {
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, curTex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, prevTex);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, FP_W, FP_H);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      // Keep the picture just drawn as the previous frame for the next call.
      gl.bindTexture(gl.TEXTURE_2D, prevTex);
      gl.copyTexImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 0, 0, FP_W, FP_H, 0);
    },
  };
}
