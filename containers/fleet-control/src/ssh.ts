// How this service connects to a device, in one place because five call sites must agree.
//
// HOST KEYS ARE NOT CHECKED, and that is a decision rather than a shortcut. These are
// physical devices on a private network that get reflashed routinely, and a reflashed card
// presents a changed key -- which is the entire reason this service once carried a way to
// FORGET a recorded key before converging, a `?reflashed=true` query parameter to trigger
// it, a play variable to do the same from the other end, and a volume to keep the record
// on. All of that existed to stop ssh refusing to connect, not to catch anything.
//
// The threat it would have guarded against is someone impersonating a campod to feed us
// camera frames and accelerometer samples. That is not on this fleet's radar, and recording
// keys only to clear them on the one occasion they would ever differ is a check with no
// failure mode left.
//
// So: record nothing, consult nothing. GlobalKnownHostsFile too, because leaving it at the
// default means a system-wide entry could still refuse a connection we have no way to fix.
export const SSH_NO_HOST_KEY_ARGS = [
  '-o', 'StrictHostKeyChecking=no',
  '-o', 'UserKnownHostsFile=/dev/null',
  '-o', 'GlobalKnownHostsFile=/dev/null',
];

/** The same, as the single string ANSIBLE_SSH_ARGS wants. */
export const SSH_NO_HOST_KEY_ARGS_STR = SSH_NO_HOST_KEY_ARGS.join(' ');
