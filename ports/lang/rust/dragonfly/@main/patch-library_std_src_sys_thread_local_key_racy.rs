--- library/std/src/sys/thread_local/key/racy.rs.orig
+++ library/std/src/sys/thread_local/key/racy.rs
@@ -21,13 +21,18 @@
 
 // Define a sentinel value that is likely not to be returned
 // as a TLS key.
-#[cfg(not(any(target_os = "nto", target_os = "qnx")))]
+#[cfg(not(any(target_os = "nto", target_os = "qnx", target_os = "dragonfly")))]
 const KEY_SENTVAL: usize = 0;
 // On QNX SDP, 0 is always returned when currently not in use.
 // Using 0 would mean to always create two keys and remove the first
 // one (with value of 0) immediately afterwards.
 #[cfg(any(target_os = "nto", target_os = "qnx"))]
 const KEY_SENTVAL: usize = libc::PTHREAD_KEYS_MAX + 1;
+// DragonFly may hand out key 0 in lean statically-linked/LTO executables.
+// Use a value outside pthread_key_t successful non-negative range instead of
+// relying on the double-create workaround for a zero sentinel.
+#[cfg(target_os = "dragonfly")]
+const KEY_SENTVAL: usize = usize::MAX;
 
 impl LazyKey {
     pub const fn new(dtor: Option<unsafe extern "C" fn(*mut u8)>) -> LazyKey {
