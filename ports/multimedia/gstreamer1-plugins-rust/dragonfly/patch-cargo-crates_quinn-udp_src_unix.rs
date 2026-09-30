--- cargo-crates/quinn-udp-0.5.14/src/unix.rs.orig
+++ cargo-crates/quinn-udp-0.5.14/src/unix.rs
@@ -1,4 +1,4 @@
-#[cfg(not(any(apple, target_os = "openbsd", solarish)))]
+#[cfg(not(any(apple, target_os = "openbsd", target_os = "dragonfly", solarish)))]
 use std::ptr;
 use std::{
     io::{self, IoSliceMut},
@@ -61,4 +61,4 @@
-#[cfg(target_os = "freebsd")]
+#[cfg(any(target_os = "freebsd", target_os = "dragonfly"))]
 type IpTosTy = libc::c_uchar;
-#[cfg(not(any(target_os = "freebsd", target_os = "netbsd")))]
+#[cfg(not(any(target_os = "freebsd", target_os = "dragonfly", target_os = "netbsd")))]
 type IpTosTy = libc::c_int;
@@ -308,3 +308,3 @@
-    #[cfg(target_os = "freebsd")]
+    #[cfg(any(target_os = "freebsd", target_os = "dragonfly"))]
     {
         let addr = io.local_addr()?;
@@ -464,3 +464,3 @@
-#[cfg(not(any(apple, target_os = "openbsd", target_os = "netbsd", solarish)))]
+#[cfg(not(any(apple, target_os = "openbsd", target_os = "netbsd", target_os = "dragonfly", solarish)))]
 fn recv(io: SockRef<'_>, bufs: &mut [IoSliceMut<'_>], meta: &mut [RecvMeta]) -> io::Result<usize> {
     let mut names = [MaybeUninit::<libc::sockaddr_storage>::uninit(); BATCH_SIZE];
@@ -541,3 +541,3 @@
-#[cfg(any(target_os = "openbsd", target_os = "netbsd", solarish, apple_slow))]
+#[cfg(any(target_os = "openbsd", target_os = "netbsd", target_os = "dragonfly", solarish, apple_slow))]
 fn recv(io: SockRef<'_>, bufs: &mut [IoSliceMut<'_>], meta: &mut [RecvMeta]) -> io::Result<usize> {
     let mut name = MaybeUninit::<libc::sockaddr_storage>::uninit();
