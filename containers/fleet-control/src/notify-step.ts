// The body of a workflow's exit handler: say how it ended.
//
// A converge is twenty minutes or more and a collect can be longer, so the ending has to
// reach the operator rather than wait in a browser tab. This used to be the run registry's
// job, inside the service -- which meant a pod roll lost the run and its notification with
// it (coordinator#566). As an `onExit` step the announcement is the workflow's own, made by
// the thing that knows how it ended, and the service holds nothing.
//
// Argo fills the arguments: the workflow's name, the action and subject it was labelled
// with at submit, its phase, and its failures. The template passes them; this formats and
// posts. `notify` never throws, so a dead Apprise cannot fail a workflow that succeeded.

import { notify, runEnded } from './notify.js';

const [workflow, action, subject, phase, failures] = process.argv.slice(2);

if (!workflow || !action || !subject || !phase) {
  console.error('usage: notify-step <workflow> <action> <subject> <phase> [failures-json]');
  process.exit(2);
}

const cfg = {
  url: process.env.FLEET_NOTIFY_URL ?? '',
  tag: process.env.FLEET_NOTIFY_TAG || 'tiles',
};

// `{{workflow.failures}}` is a JSON list of the failed nodes with their messages, or an
// empty string on success. One line of it is what a notification can carry.
let detail: string | undefined;
if (failures !== undefined && failures !== '' && failures !== '[]') {
  try {
    const list = JSON.parse(failures) as Array<{ displayName?: string; message?: string }>;
    detail = list
      .map((f) => `${f.displayName ?? '?'}: ${f.message ?? 'failed'}`)
      .join('\n')
      .slice(0, 500);
  } catch {
    detail = failures.slice(0, 500);
  }
}

const [title, body] = runEnded(action, subject, phase.toLowerCase(), detail);
console.log(`[notify-step] ${workflow}: ${title}`);
await notify(cfg, title, `${body}\n\n${workflow}`);
