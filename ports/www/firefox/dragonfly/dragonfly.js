// DragonFly: fission (site isolation) off -- e10s multiprocess stays on,
// but per-site process spawning triggers a fork/pthread-atfork crash and
// is slow on DragonFly. See www/firefox overlay.
pref("fission.autostart", false);

// Use the fork server so content/RDD processes are forked from a clean
// single-threaded process -- DragonFly rtld does not reset its lock across a
// multithreaded fork, which otherwise deadlocks/crashes child spawning.
pref("dom.ipc.forkserver.enable", true);
