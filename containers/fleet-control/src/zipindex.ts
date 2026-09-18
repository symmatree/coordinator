// Read the member names out of a zip without extracting it.
//
// We need the `.img` name inside the artifact, because that is what the device's manifest
// reports as its own identity -- so reading it from the artifact means our value cannot drift
// from what ends up on the card. Constructing it from a naming convention could.
//
// The files are ~800 MiB, so this reads the central directory at the end of the file rather
// than streaming the whole thing. No dependency: we need filenames and nothing else, and a
// zip reader is a larger thing to carry than the ~60 lines that answers the question.

import { open } from 'node:fs/promises';

const EOCD_SIG = 0x06054b50;
const CD_SIG = 0x02014b50;
/** EOCD is 22 bytes plus a comment of at most 64 KiB, so it lives in the last chunk. */
const TAIL = 22 + 0xffff;

/** Member names, in central-directory order. */
export async function zipMemberNames(path: string): Promise<string[]> {
  const fh = await open(path, 'r');
  try {
    const { size } = await fh.stat();
    const tailLen = Math.min(size, TAIL);
    const tail = Buffer.alloc(tailLen);
    await fh.read(tail, 0, tailLen, size - tailLen);

    let eocd = -1;
    for (let i = tail.length - 22; i >= 0; i--) {
      if (tail.readUInt32LE(i) === EOCD_SIG) {
        eocd = i;
        break;
      }
    }
    if (eocd < 0) throw new Error(`not a zip (no end-of-central-directory): ${path}`);

    const count = tail.readUInt16LE(eocd + 10);
    const cdSize = tail.readUInt32LE(eocd + 12);
    const cdOffset = tail.readUInt32LE(eocd + 16);
    if (cdOffset === 0xffffffff || cdSize === 0xffffffff) {
      throw new Error(`zip64 central directory not supported: ${path}`);
    }

    const cd = Buffer.alloc(cdSize);
    await fh.read(cd, 0, cdSize, cdOffset);

    const names: string[] = [];
    let p = 0;
    for (let i = 0; i < count && p + 46 <= cd.length; i++) {
      if (cd.readUInt32LE(p) !== CD_SIG) break;
      const nameLen = cd.readUInt16LE(p + 28);
      const extraLen = cd.readUInt16LE(p + 30);
      const commentLen = cd.readUInt16LE(p + 32);
      names.push(cd.subarray(p + 46, p + 46 + nameLen).toString('utf8'));
      p += 46 + nameLen + extraLen + commentLen;
    }
    return names;
  } finally {
    await fh.close();
  }
}

/**
 * The single `.img` member. The image build uploads exactly one per role artifact; anything
 * else means the artifact is not what we think it is, which is worth failing on rather than
 * guessing between.
 */
export async function imgMemberName(path: string): Promise<string> {
  const imgs = (await zipMemberNames(path)).filter((n) => n.endsWith('.img'));
  const [only] = imgs;
  if (imgs.length !== 1 || only === undefined) {
    throw new Error(`expected exactly one .img in ${path}, found ${imgs.length}`);
  }
  return only;
}
