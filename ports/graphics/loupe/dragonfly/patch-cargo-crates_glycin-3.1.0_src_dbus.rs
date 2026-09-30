--- cargo-crates/glycin-3.1.0/src/dbus.rs.orig
+++ cargo-crates/glycin-3.1.0/src/dbus.rs
@@ -637,6 +637,7 @@
     }
 }
 
+#[cfg(not(target_os = "dragonfly"))]
 async fn seal_fd(fd: impl AsRawFd) -> Result<(), memfd::Error> {
     let raw_fd = fd.as_raw_fd();
 
@@ -666,7 +667,12 @@
         }
     }
     mem::forget(mfd);
+
+    Ok(())
+}
 
+#[cfg(target_os = "dragonfly")]
+async fn seal_fd(_fd: impl AsRawFd) -> Result<(), Error> {
     Ok(())
 }
 
