--- cargo-crates/glycin-3.1.0/src/lib.rs.orig
+++ cargo-crates/glycin-3.1.0/src/lib.rs
@@ -73,7 +73,79 @@
 mod icc;
 mod orientation;
 mod pool;
+#[cfg(not(target_os = "dragonfly"))]
 mod sandbox;
+#[cfg(target_os = "dragonfly")]
+mod sandbox {
+    use std::os::fd::{AsRawFd, BorrowedFd};
+    use std::os::unix::net::UnixStream;
+    use std::os::unix::process::CommandExt;
+    use std::path::PathBuf;
+    use std::process::{Command, Stdio};
+
+    use crate::config::ConfigEntry;
+    use crate::{Error, SandboxMechanism};
+
+    pub struct Sandbox {
+        config_entry: ConfigEntry,
+        dbus_socket: UnixStream,
+    }
+
+    pub struct SpawnedSandbox {
+        pub command: Command,
+        pub _dbus_socket: UnixStream,
+    }
+
+    impl Sandbox {
+        pub fn new(
+            _sandbox_mechanism: SandboxMechanism,
+            config_entry: ConfigEntry,
+            dbus_socket: UnixStream,
+        ) -> Self {
+            Self {
+                config_entry,
+                dbus_socket,
+            }
+        }
+
+        pub fn add_ro_bind(&mut self, _path: PathBuf) {}
+
+        pub async fn check_bwrap_syscalls_blocked() -> bool {
+            false
+        }
+
+        pub async fn spawn(self) -> Result<SpawnedSandbox, Error> {
+            let dbus_fd = self.dbus_socket.as_raw_fd();
+            let mut command = Command::new(self.config_entry.exec());
+            command.env_clear();
+            for key in ["RUST_BACKTRACE", "RUST_LOG", "XDG_RUNTIME_DIR"] {
+                if let Some(value) = std::env::var_os(key) {
+                    command.env(key, value);
+                }
+            }
+            command.arg("--dbus-fd").arg(dbus_fd.to_string());
+            command.stdin(Stdio::piped());
+            command.stderr(Stdio::piped());
+            command.stdout(Stdio::piped());
+
+            unsafe {
+                command.pre_exec(move || {
+                    let fd = BorrowedFd::borrow_raw(dbus_fd);
+                    let flags = nix::fcntl::fcntl(fd, nix::fcntl::FcntlArg::F_GETFD)?;
+                    let mut flags = nix::fcntl::FdFlag::from_bits_truncate(flags);
+                    flags.remove(nix::fcntl::FdFlag::FD_CLOEXEC);
+                    nix::fcntl::fcntl(fd, nix::fcntl::FcntlArg::F_SETFD(flags))?;
+                    Ok(())
+                });
+            }
+
+            Ok(SpawnedSandbox {
+                command,
+                _dbus_socket: self.dbus_socket,
+            })
+        }
+    }
+}
 mod util;
 
 #[cfg(feature = "gobject")]
