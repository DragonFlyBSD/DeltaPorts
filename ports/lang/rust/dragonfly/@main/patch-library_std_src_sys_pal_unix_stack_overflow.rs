--- library/std/src/sys/pal/unix/stack_overflow.rs.orig
+++ library/std/src/sys/pal/unix/stack_overflow.rs
@@ -31,6 +31,7 @@
     any(
         target_os = "linux",
         target_os = "freebsd",
+        target_os = "dragonfly",
         target_os = "hurd",
         target_os = "macos",
         target_os = "netbsd",
@@ -49,6 +50,7 @@
     any(
         target_os = "linux",
         target_os = "freebsd",
+        target_os = "dragonfly",
         target_os = "hurd",
         target_os = "macos",
         target_os = "netbsd",
@@ -396,6 +398,7 @@
     #[cfg(any(
         target_os = "android",
         target_os = "freebsd",
+        target_os = "dragonfly",
         target_os = "netbsd",
         target_os = "hurd",
         target_os = "linux",
@@ -407,7 +410,7 @@
 
         let mut ret = None;
         let mut attr: COpaque<libc::pthread_attr_t> = COpaque::uninit();
-        if !cfg!(target_os = "freebsd") {
+        if !cfg!(any(target_os = "freebsd", target_os = "dragonfly")) {
             attr = COpaque::zeroed();
         }
         let attr = pin!(attr);
@@ -417,17 +420,17 @@
         // SAFETY:
         // The attributes object has not been initialized yet and will not be
         // moved until destroyed.
-        #[cfg(target_os = "freebsd")]
+        #[cfg(any(target_os = "freebsd", target_os = "dragonfly"))]
         assert_eq!(unsafe { libc::pthread_attr_init(attr.get()) }, 0);
         // SAFETY:
         // * calling `pthread_self` is always valid and returns a valid `pthread_t`
         // * `attr` is an initialized attribute object that can be written to.
-        #[cfg(target_os = "freebsd")]
+        #[cfg(any(target_os = "freebsd", target_os = "dragonfly"))]
         let e = unsafe { libc::pthread_attr_get_np(libc::pthread_self(), attr.get()) };
         // SAFETY:
         // * calling `pthread_self` is always valid and returns a valid `pthread_t`
         // * `attr` can be written to, and will be initialized by this call.
-        #[cfg(not(target_os = "freebsd"))]
+        #[cfg(not(any(target_os = "freebsd", target_os = "dragonfly")))]
         let e = unsafe { libc::pthread_getattr_np(libc::pthread_self(), attr.get()) };
         if e == 0 {
             let mut stackaddr = crate::ptr::null_mut();
@@ -441,7 +444,7 @@
             );
             ret = Some(stackaddr);
         }
-        if e == 0 || cfg!(target_os = "freebsd") {
+        if e == 0 || cfg!(any(target_os = "freebsd", target_os = "dragonfly")) {
             // SAFETY:
             // `attr` was initialized either by `pthread_attr_init` (FreeBSD) or
             // by `pthread_attr_get_np`, and is not used after this point.
@@ -485,7 +488,7 @@
             // The FreeBSD code cannot be checked on non-BSDs.
             #[cfg(target_os = "freebsd")]
             install_main_guard_freebsd(page_size)
-        } else if cfg!(any(target_os = "netbsd", target_os = "openbsd")) {
+        } else if cfg!(any(target_os = "dragonfly", target_os = "netbsd", target_os = "openbsd")) {
             install_main_guard_bsds(page_size)
         } else {
             // SAFETY: guaranteed by caller.
@@ -638,6 +641,7 @@
     #[cfg(any(
         target_os = "android",
         target_os = "freebsd",
+        target_os = "dragonfly",
         target_os = "hurd",
         target_os = "linux",
         target_os = "netbsd",
@@ -650,7 +654,7 @@
         let mut ret = None;
 
         let mut attr: COpaque<libc::pthread_attr_t> = COpaque::uninit();
-        if !cfg!(target_os = "freebsd") {
+        if !cfg!(any(target_os = "freebsd", target_os = "dragonfly")) {
             attr = COpaque::zeroed();
         }
         let attr = pin!(attr);
@@ -660,17 +664,17 @@
         // SAFETY:
         // The attributes object has not been initialized yet and will not be
         // moved until destroyed.
-        #[cfg(target_os = "freebsd")]
+        #[cfg(any(target_os = "freebsd", target_os = "dragonfly"))]
         assert_eq!(unsafe { libc::pthread_attr_init(attr.get()) }, 0);
         // SAFETY:
         // * calling `pthread_self` is always valid and returns a valid `pthread_t`
         // * `attr` is an initialized attribute object that can be written to.
-        #[cfg(target_os = "freebsd")]
+        #[cfg(any(target_os = "freebsd", target_os = "dragonfly"))]
         let e = unsafe { libc::pthread_attr_get_np(libc::pthread_self(), attr.get()) };
         // SAFETY:
         // * calling `pthread_self` is always valid and returns a valid `pthread_t`
         // * `attr` can be written to, and will be initialized by this call.
-        #[cfg(not(target_os = "freebsd"))]
+        #[cfg(not(any(target_os = "freebsd", target_os = "dragonfly")))]
         let e = unsafe { libc::pthread_getattr_np(libc::pthread_self(), attr.get()) };
         if e == 0 {
             let mut guardsize = 0;
@@ -699,7 +703,7 @@
             );
 
             let stackaddr = stackptr.addr();
-            ret = if cfg!(any(target_os = "freebsd", target_os = "netbsd", target_os = "hurd")) {
+            ret = if cfg!(any(target_os = "freebsd", target_os = "dragonfly", target_os = "netbsd", target_os = "hurd")) {
                 Some(stackaddr - guardsize..stackaddr)
             } else if cfg!(all(target_os = "linux", target_env = "musl")) {
                 Some(stackaddr - guardsize..stackaddr)
@@ -716,7 +720,7 @@
                 Some(stackaddr..stackaddr + guardsize)
             };
         }
-        if e == 0 || cfg!(target_os = "freebsd") {
+        if e == 0 || cfg!(any(target_os = "freebsd", target_os = "dragonfly")) {
             // SAFETY:
             // `attr` was initialized either by `pthread_attr_init` (FreeBSD) or
             // by `pthread_attr_get_np`, and is not used after this point.
@@ -739,6 +743,7 @@
     not(any(
         target_os = "linux",
         target_os = "freebsd",
+        target_os = "dragonfly",
         target_os = "hurd",
         target_os = "macos",
         target_os = "netbsd",
