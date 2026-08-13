// silk 解码: 读入微信 .silk 文件, stdout 输出 "RATE" + uint32le 采样率 + int16le PCM
// 用法: node tools/silk_decode.js input.silk [outRate=24000] > out.pcm
// 依赖: silk-wasm (装在托管 node workspace, 通过 NODE_PATH 提供)
const fs = require("fs");
const { decode } = require("silk-wasm");

const inPath = process.argv[2];
const outRate = parseInt(process.argv[3] || "24000", 10);
if (!inPath) {
  console.error("usage: node silk_decode.js input.silk [outRate]");
  process.exit(2);
}

(async () => {
  let buf = fs.readFileSync(inPath);
  // 归一化: 定位 #!SILK_V3 魔数, 前面保留恰好一个 0x02 (silk-wasm 期望微信格式)
  const magic = Buffer.from("#!SILK_V3");
  const i = buf.indexOf(magic);
  if (i < 0) {
    console.error("silk decode failed: not a SILK file (no #!SILK_V3 magic)");
    process.exit(1);
  }
  buf = Buffer.concat([Buffer.from([0x02]), buf.slice(i)]);
  const { data } = await decode(buf, outRate); // data = pcm_s16le @ outRate
  const header = Buffer.alloc(8);
  header.write("RATE", 0, "latin1");
  header.writeUInt32LE(outRate, 4);
  process.stdout.write(header);
  process.stdout.write(Buffer.from(data));
})().catch((e) => {
  console.error("silk decode failed:", e.message);
  process.exit(1);
});
