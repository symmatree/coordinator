// What the FC has been saying over mLRS: its status text, arm and disarm, and the result of any
// arm command sent over that link. The motivating question is "why won't it arm?" -- the answer
// is the PreArm and Arm text the FC sends, and nobody standing at the vehicle can read it.
//
// READ FROM THE SESSION TLOG, NOT FROM A LINK. mavproxy-mlrs already forwards every frame to
// tlog-split-mlrs, which writes `mlrs/<start>.tlog.part` on the ground-tlog share this service
// already mounts. The file is the feed: no second path from mavproxy, no port to claim (its TCP
// port takes one client, and that is Mission Planner's), and every frame carries the time the
// ground received it. tlog-split flushes the open file once a second (tiles#936).
//
// Read on request, from where the last request stopped. Nothing polls in the background: when
// nobody is asking, nothing is read. The first request reads the open file from its start,
// because a tlog can only be framed from the beginning; after that each request reads only
// what was appended. When tlog-split rolls -- at disarm, or on its age and size bounds -- the
// open file is renamed with its outcome and a new one starts. The rest of the old file is read
// under its new name before moving on, so the messages around a disarm are not lost to the cut.
//
// What it shows depends on what the FC sends down mLRS. STATUSTEXT does not go to a private
// MAVLink channel (coordinator#683, item 1), so until that changes this shows arm state and
// command results only.

import { open, readdir } from 'node:fs/promises';
import { join } from 'node:path';

/** One thing the FC said or did, newest first in the report. */
export interface FcEvent {
  /** When the ground received it, from the tlog record. */
  t: string;
  kind: 'text' | 'armed' | 'disarmed' | 'arm-command';
  /** MAV_SEVERITY name for text; the MAV_RESULT name for an arm command. */
  severity?: string;
  text: string;
  sysid: number;
  compid: number;
}

export interface FcStatus {
  dir: string;
  /** The file being followed, and how far into it this has read. */
  file?: string;
  offset?: number;
  /** From the autopilot's HEARTBEAT. Undefined until one has been seen. */
  armed?: boolean;
  /** The newest frame of any kind from the FC: is the link up at all? */
  lastHeard?: string;
  events: FcEvent[];
  error?: string;
}

const SEVERITY = ['emergency', 'alert', 'critical', 'error', 'warning', 'notice', 'info', 'debug'];
const RESULT = ['accepted', 'temporarily rejected', 'denied', 'unsupported', 'failed', 'in progress', 'cancelled'];

const HEARTBEAT = 0;
const COMMAND_ACK = 77;
const STATUSTEXT = 253;
const MAV_CMD_COMPONENT_ARM_DISARM = 400;
const MAV_AUTOPILOT_ARDUPILOTMEGA = 3;
const MAV_MODE_FLAG_SAFETY_ARMED = 0x80;

/** How many events are kept. A PreArm failure repeats every 30 s; this is most of an hour. */
const KEEP = 200;

/** One MAVLink frame out of a tlog, with the ground's receive time. */
export interface Frame {
  t: number; // ms since the epoch
  sysid: number;
  compid: number;
  msgid: number;
  payload: Buffer;
}

/**
 * The whole frames in `buf`, and how many bytes they took. A record cut off by the end of the
 * buffer is left for the next read. Each tlog record is an 8-byte big-endian microsecond stamp
 * followed by one MAVLink v1 or v2 frame; a record that does not start a frame is skipped a
 * byte at a time until one does.
 */
export function parseTlog(buf: Buffer): { frames: Frame[]; used: number } {
  const frames: Frame[] = [];
  let i = 0;
  const u8 = (at: number) => buf.readUInt8(at);
  while (i + 8 + 3 <= buf.length) {
    const stx = u8(i + 8);
    const len = u8(i + 9);
    let header: number, tail: number;
    if (stx === 0xfd) {
      header = 10;
      tail = 2 + ((u8(i + 10) & 0x01) ? 13 : 0);
    } else if (stx === 0xfe) {
      header = 6;
      tail = 2;
    } else {
      i += 1;
      continue;
    }
    const end = i + 8 + header + len + tail;
    if (end > buf.length) break;
    const f = i + 8;
    const t = Number(buf.readBigUInt64BE(i) / 1000n);
    if (stx === 0xfd) {
      frames.push({
        t, sysid: u8(f + 5), compid: u8(f + 6),
        msgid: u8(f + 7) | (u8(f + 8) << 8) | (u8(f + 9) << 16),
        payload: buf.subarray(f + header, f + header + len),
      });
    } else {
      frames.push({ t, sysid: u8(f + 3), compid: u8(f + 4), msgid: u8(f + 5), payload: buf.subarray(f + header, f + header + len) });
    }
    i = end;
  }
  return { frames, used: i };
}

/** A v2 payload has its trailing zeros cut; put them back so fields read at fixed offsets. */
function padded(p: Buffer, size: number): Buffer {
  return p.length >= size ? p : Buffer.concat([p, Buffer.alloc(size - p.length)]);
}

/** Follows the open mLRS tlog and keeps what the FC said. One per service. */
export class FcStatusReader {
  private file?: string;
  private offset = 0;
  private armed?: boolean;
  private lastHeard?: number;
  private events: FcEvent[] = [];
  /** A STATUSTEXT longer than 50 characters arrives in chunks sharing an id. */
  private chunk?: { key: string; seq: number; event: FcEvent };
  private busy: Promise<unknown> = Promise.resolve();

  constructor(readonly dir: string) {}

  /** Read what has been appended since the last call, and report. Calls are serialised. */
  status(): Promise<FcStatus> {
    const next = this.busy.then(() => this.update());
    this.busy = next.catch(() => undefined);
    return next;
  }

  private async update(): Promise<FcStatus> {
    let error: string | undefined;
    try {
      const names = (await readdir(this.dir)).sort();
      const open = names.filter((n) => n.endsWith('.tlog.part')).at(-1);
      if (this.file !== undefined && this.file !== open) {
        // The file we were reading has been closed and renamed: same start stamp, outcome
        // appended. Finish it under its new name, then start on the new one.
        const stamp = this.file.slice(0, 16);
        const closed = names.find((n) => n.startsWith(stamp) && !n.endsWith('.part'));
        if (closed !== undefined) await this.readFrom(closed);
        this.file = undefined;
        this.offset = 0;
      }
      if (open === undefined) {
        error = `no open tlog in ${this.dir}`;
      } else {
        this.file = open;
        await this.readFrom(open);
      }
    } catch (err) {
      error = (err as Error).message;
    }
    return {
      dir: this.dir,
      file: this.file,
      offset: this.file === undefined ? undefined : this.offset,
      armed: this.armed,
      lastHeard: this.lastHeard === undefined ? undefined : new Date(this.lastHeard).toISOString(),
      events: [...this.events].reverse(),
      error,
    };
  }

  private async readFrom(name: string): Promise<void> {
    const fh = await open(join(this.dir, name), 'r');
    try {
      const size = (await fh.stat()).size;
      if (size <= this.offset) return;
      const buf = Buffer.alloc(size - this.offset);
      const { bytesRead } = await fh.read(buf, 0, buf.length, this.offset);
      const { frames, used } = parseTlog(buf.subarray(0, bytesRead));
      this.offset += used;
      for (const f of frames) this.take(f);
    } finally {
      await fh.close();
    }
  }

  /** The FC is whichever system sends an ArduPilot HEARTBEAT; everything is kept from it. */
  private fcSysid?: number;

  private take(f: Frame): void {
    const t = new Date(f.t).toISOString();
    if (f.msgid === HEARTBEAT) {
      const p = padded(f.payload, 9);
      if (p.readUInt8(5) !== MAV_AUTOPILOT_ARDUPILOTMEGA) return;
      this.fcSysid = f.sysid;
      this.lastHeard = f.t;
      const armed = (p.readUInt8(6) & MAV_MODE_FLAG_SAFETY_ARMED) !== 0;
      if (this.armed !== undefined && armed !== this.armed) {
        this.push({ t, kind: armed ? 'armed' : 'disarmed', text: armed ? 'armed' : 'disarmed', sysid: f.sysid, compid: f.compid });
      }
      this.armed = armed;
      return;
    }
    if (f.sysid === this.fcSysid) this.lastHeard = f.t;
    if (f.msgid === STATUSTEXT) {
      const p = padded(f.payload, 54);
      const raw = p.subarray(1, 51);
      const nul = raw.indexOf(0);
      const text = raw.subarray(0, nul < 0 ? 50 : nul).toString('latin1');
      const id = p.readUInt16LE(51);
      const seq = p.readUInt8(53);
      const severity = p.readUInt8(0);
      const key = `${f.sysid}/${f.compid}/${id}`;
      if (id !== 0 && seq > 0 && this.chunk?.key === key && this.chunk.seq === seq - 1) {
        this.chunk.event.text += text;
        this.chunk.seq = seq;
        return;
      }
      const event: FcEvent = { t, kind: 'text', severity: SEVERITY[severity] ?? String(severity), text, sysid: f.sysid, compid: f.compid };
      this.chunk = id !== 0 ? { key, seq, event } : undefined;
      this.push(event);
      return;
    }
    if (f.msgid === COMMAND_ACK) {
      const p = padded(f.payload, 3);
      if (p.readUInt16LE(0) !== MAV_CMD_COMPONENT_ARM_DISARM) return;
      const code = p.readUInt8(2);
      const result = RESULT[code] ?? String(code);
      this.push({ t, kind: 'arm-command', severity: result, text: `arm/disarm command ${result}`, sysid: f.sysid, compid: f.compid });
    }
  }

  private push(e: FcEvent): void {
    this.events.push(e);
    if (this.events.length > KEEP) this.events.splice(0, this.events.length - KEEP);
  }
}
