// One text file in a ZIP, built in the browser. Entries are stored (not compressed), so the bytes inside are exactly the text given.
const TABLE=(()=>{const t=new Uint32Array(256);for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;t[n]=c>>>0}return t})();
export function crc32(bytes){let c=0xFFFFFFFF;for(let i=0;i<bytes.length;i++)c=TABLE[(c^bytes[i])&0xFF]^(c>>>8);return (c^0xFFFFFFFF)>>>0}
export function zipText(name,text,when=new Date()){
  const enc=new TextEncoder(),nameBytes=enc.encode(name),data=enc.encode(text),crc=crc32(data);
  const year=Math.max(1980,when.getFullYear()),dosTime=(when.getHours()<<11)|(when.getMinutes()<<5)|(when.getSeconds()>>1),dosDate=((year-1980)<<9)|((when.getMonth()+1)<<5)|when.getDate();
  const local=new DataView(new ArrayBuffer(30)),central=new DataView(new ArrayBuffer(46)),end=new DataView(new ArrayBuffer(22));
  local.setUint32(0,0x04034b50,true);local.setUint16(4,20,true);local.setUint16(6,0x0800,true);local.setUint16(8,0,true);local.setUint16(10,dosTime,true);local.setUint16(12,dosDate,true);
  local.setUint32(14,crc,true);local.setUint32(18,data.length,true);local.setUint32(22,data.length,true);local.setUint16(26,nameBytes.length,true);local.setUint16(28,0,true);
  central.setUint32(0,0x02014b50,true);central.setUint16(4,20,true);central.setUint16(6,20,true);central.setUint16(8,0x0800,true);central.setUint16(10,0,true);central.setUint16(12,dosTime,true);central.setUint16(14,dosDate,true);
  central.setUint32(16,crc,true);central.setUint32(20,data.length,true);central.setUint32(24,data.length,true);central.setUint16(28,nameBytes.length,true);central.setUint32(42,0,true);
  const centralSize=46+nameBytes.length,centralStart=30+nameBytes.length+data.length;
  end.setUint32(0,0x06054b50,true);end.setUint16(8,1,true);end.setUint16(10,1,true);end.setUint32(12,centralSize,true);end.setUint32(16,centralStart,true);
  const out=new Uint8Array(centralStart+centralSize+22);let at=0;
  for(const part of [new Uint8Array(local.buffer),nameBytes,data,new Uint8Array(central.buffer),nameBytes,new Uint8Array(end.buffer)]){out.set(part,at);at+=part.length}
  return out;
}
