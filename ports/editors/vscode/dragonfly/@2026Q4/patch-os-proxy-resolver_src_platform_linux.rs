--- os-proxy-resolver/src/platform/linux.rs.orig
+++ os-proxy-resolver/src/platform/linux.rs
@@ -220,7 +220,7 @@
             if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) == -1 {
                 return Err(std::io::Error::last_os_error());
             }
-            #[cfg(target_os = "freebsd")]
+            #[cfg(any(target_os = "freebsd", target_os = "dragonfly"))]
             {
                 let mut sig = libc::SIGKILL;
                 let ret = libc::procctl(
@@ -261,6 +261,8 @@
     // succeeds while preventing every non-stdio descriptor from reaching the
     // watcher program.
     // SAFETY: close_range operates on the calling process's descriptor table.
+    // DragonFly has no close_range: straight to the fcntl fallback.
+    #[cfg(any(target_os = "linux", target_os = "freebsd"))]
     loop {
         #[cfg(target_os = "linux")]
         let result = unsafe {
