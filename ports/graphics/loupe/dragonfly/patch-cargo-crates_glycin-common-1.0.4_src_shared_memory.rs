--- cargo-crates/glycin-common-1.0.4/src/shared_memory.rs.orig
+++ cargo-crates/glycin-common-1.0.4/src/shared_memory.rs
@@ -1,5 +1,11 @@
 use std::ops::{Deref, DerefMut};
 use std::os::fd::{AsRawFd, OwnedFd};
+#[cfg(target_os = "dragonfly")]
+use std::os::fd::FromRawFd;
+#[cfg(target_os = "dragonfly")]
+use std::sync::atomic::{AtomicU64, Ordering};
+#[cfg(target_os = "dragonfly")]
+use std::{ffi::CString, process};
 
 use crate::{BinaryData, Error};
 
@@ -11,6 +17,8 @@
 
 impl SharedMemory {
     pub fn new(size: u64) -> Result<Self, Error> {
+        #[cfg(not(target_os = "dragonfly"))]
+        {
         let memfd = nix::sys::memfd::memfd_create(
             c"glycin-frame",
             nix::sys::memfd::MFdFlags::MFD_CLOEXEC | nix::sys::memfd::MFdFlags::MFD_ALLOW_SEALING,
@@ -23,7 +31,46 @@
         let raw_fd = memfd.as_raw_fd();
         let mmap = unsafe { memmap::MmapMut::map_mut(raw_fd) }?;
 
-        Ok(Self { mmap, memfd })
+        return Ok(Self { mmap, memfd });
+        }
+
+        #[cfg(target_os = "dragonfly")]
+        {
+            static NEXT_SHM_ID: AtomicU64 = AtomicU64::new(0);
+
+            let name = format!(
+                "/glycin-frame-{}-{}",
+                process::id(),
+                NEXT_SHM_ID.fetch_add(1, Ordering::Relaxed)
+            );
+            let name = CString::new(name).expect("generated shared-memory name has no NUL");
+            let raw_fd = unsafe {
+                nix::libc::shm_open(
+                    name.as_ptr(),
+                    nix::libc::O_CREAT | nix::libc::O_EXCL | nix::libc::O_RDWR,
+                    0o600,
+                )
+            };
+            if raw_fd < 0 {
+                return Err(std::io::Error::last_os_error().into());
+            }
+
+            if unsafe { nix::libc::shm_unlink(name.as_ptr()) } < 0 {
+                let error = std::io::Error::last_os_error();
+                unsafe { nix::libc::close(raw_fd) };
+                return Err(error.into());
+            }
+
+            let memfd = unsafe { OwnedFd::from_raw_fd(raw_fd) };
+            let size = nix::libc::off_t::try_from(size)
+                .expect("required shared memory is too large");
+            nix::unistd::ftruncate(&memfd, size)
+                .expect("failed to set shared memory size");
+
+            let mmap = unsafe { memmap::MmapMut::map_mut(memfd.as_raw_fd()) }?;
+
+            Ok(Self { mmap, memfd })
+        }
     }
 
     pub fn into_binary_data(self) -> BinaryData {
