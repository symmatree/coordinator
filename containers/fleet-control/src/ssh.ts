// How this service connects to a device, in one place because five call sites must agree.
//
// HOST KEYS ARE NOT CHECKED, and that is a decision rather than a shortcut. These are
// physical devices on a private network that get reflashed routinely, and a reflashed card
// presents a CHANGED key, which no amount of accept-new accepts -- so checking only ever
// refuses a card that has just been written. The threat it would guard against is someone
// impersonating a campod to feed us camera frames and accelerometer samples, which is not
// on this fleet's radar.
//
// GlobalKnownHostsFile too: left at the default, a system-wide entry could still refuse a
// connection with nothing to clear.
export const SSH_NO_HOST_KEY_ARGS = [
  '-o', 'StrictHostKeyChecking=no',
  '-o', 'UserKnownHostsFile=/dev/null',
  '-o', 'GlobalKnownHostsFile=/dev/null',
];

/** The same, as the single string ANSIBLE_SSH_ARGS wants. */
export const SSH_NO_HOST_KEY_ARGS_STR = SSH_NO_HOST_KEY_ARGS.join(' ');
