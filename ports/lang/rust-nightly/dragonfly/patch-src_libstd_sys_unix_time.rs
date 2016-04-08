--- src/libstd/sys/unix/time.rs.orig	2016-04-08 10:56:44 UTC
+++ src/libstd/sys/unix/time.rs
@@ -304,7 +304,7 @@ mod inner {
     }
 
     impl Timespec {
-        pub fn now(clock: libc::c_int) -> Timespec {
+        pub fn now(clock: libc::c_ulong) -> Timespec {
             let mut t = Timespec {
                 t: libc::timespec {
                     tv_sec: 0,
