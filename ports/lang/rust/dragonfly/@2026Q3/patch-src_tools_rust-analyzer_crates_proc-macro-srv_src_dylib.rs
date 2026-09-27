--- src/tools/rust-analyzer/crates/proc-macro-srv/src/dylib.rs.orig
+++ src/tools/rust-analyzer/crates/proc-macro-srv/src/dylib.rs
@@ -248,6 +248,14 @@
     #[cfg(not(target_env = "gnu"))]
     const RTLD_DEEPBIND: std::os::raw::c_int = 0x0;

+    const RTLD_NODELETE: std::os::raw::c_int = 0x01000;
+
+    let flags = if cfg!(target_os = "dragonfly") {
+        RTLD_NOW | RTLD_NODELETE
+    } else {
+        RTLD_NOW | RTLD_DEEPBIND
+    };
+
     // SAFETY: The caller is responsible for ensuring that the path is valid proc-macro library
-    unsafe { UnixLibrary::open(Some(file), RTLD_NOW | RTLD_DEEPBIND).map(|lib| lib.into()) }
+    unsafe { UnixLibrary::open(Some(file), flags).map(|lib| lib.into()) }
 }
