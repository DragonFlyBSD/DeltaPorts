--- src/liblibc/src/unix/bsd/freebsdlike/mod.rs.orig	2016-03-07 17:09:06 UTC
+++ src/liblibc/src/unix/bsd/freebsdlike/mod.rs
@@ -366,8 +366,8 @@ pub const _SC_XOPEN_XCU_VERSION: ::c_int
 pub const PTHREAD_CREATE_JOINABLE: ::c_int = 0;
 pub const PTHREAD_CREATE_DETACHED: ::c_int = 1;
 
-pub const CLOCK_REALTIME: ::c_int = 0;
-pub const CLOCK_MONOTONIC: ::c_int = 4;
+pub const CLOCK_REALTIME: ::c_ulong = 0;
+pub const CLOCK_MONOTONIC: ::c_ulong = 4;
 
 pub const RLIMIT_CPU: ::c_int = 0;
 pub const RLIMIT_FSIZE: ::c_int = 1;
