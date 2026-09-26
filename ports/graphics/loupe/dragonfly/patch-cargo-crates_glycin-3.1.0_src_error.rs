--- cargo-crates/glycin-3.1.0/src/error.rs.orig
+++ cargo-crates/glycin-3.1.0/src/error.rs
@@ -6,6 +6,7 @@
 use gio::glib;
 use gio::prelude::CancellableExt;
 use glycin_utils::{DimensionTooLargerError, RemoteError};
+#[cfg(not(target_os = "dragonfly"))]
 use libseccomp::error::SeccompError;
 
 use crate::config;
@@ -179,8 +180,10 @@
     StrideTooSmall(String),
     #[error("Width or height is zero: {0}")]
     WidgthOrHeightZero(String),
+    #[cfg(not(target_os = "dragonfly"))]
     #[error("Memfd: {0}")]
     MemFd(Arc<memfd::Error>),
+    #[cfg(not(target_os = "dragonfly"))]
     #[error("Seccomp: {0}")]
     Seccomp(Arc<SeccompError>),
     #[error("ICC profile: {0}")]
@@ -240,12 +243,14 @@
     }
 }
 
+#[cfg(not(target_os = "dragonfly"))]
 impl From<memfd::Error> for Error {
     fn from(err: memfd::Error) -> Self {
         Self::MemFd(Arc::new(err))
     }
 }
 
+#[cfg(not(target_os = "dragonfly"))]
 impl From<SeccompError> for Error {
     fn from(err: SeccompError) -> Self {
         Self::Seccomp(Arc::new(err))
