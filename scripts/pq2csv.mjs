// Convert parquet → CSV using hyparquet from source. Usage: node pq2csv.mjs out.csv file1.parquet file2.parquet ...
import { readFileSync, writeFileSync, appendFileSync } from 'node:fs';
import { parquetReadObjects, parquetMetadata, parquetSchema } from './hyparquet/src/index.js';
const [out, ...files] = process.argv.slice(2);
let header = false;
writeFileSync(out, '');
for (const f of files) {
  const buf = readFileSync(f);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  const rows = await parquetReadObjects({ file: ab });
  if (!rows.length) continue;
  const cols = Object.keys(rows[0]);
  if (!header) { appendFileSync(out, cols.join(',') + '\n'); header = true; }
  const lines = rows.map(r => cols.map(c => { const v = r[c]; return v instanceof Date ? v.getTime() : (typeof v === 'bigint' ? v.toString() : v); }).join(',')).join('\n') + '\n';
  appendFileSync(out, lines);
}
console.error('rows written from', files.length, 'files');
