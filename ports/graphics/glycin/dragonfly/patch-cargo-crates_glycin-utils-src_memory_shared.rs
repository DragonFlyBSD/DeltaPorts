--- cargo-crates/glycin-utils-5.0.0-alpha/src/memory/shared.rs.orig
+++ cargo-crates/glycin-utils-5.0.0-alpha/src/memory/shared.rs
@@ -1,7 +1,15 @@
 use std::ops::{Deref, DerefMut};
 use std::os::fd::{AsRawFd, OwnedFd};
 
+#[cfg(target_os = "dragonfly")]
+use std::os::fd::FromRawFd;
+#[cfg(target_os = "dragonfly")]
+use std::sync::atomic::{AtomicU64, Ordering};
+#[cfg(target_os = "dragonfly")]
+use std::{ffi::CString, process};
+
 use log::warn;
+#[cfg(not(target_os = "dragonfly"))]
 use nix::fcntl;
 use serde::{Deserialize, Deserializer, Serialize, Serializer};
 use zbus::zvariant;
@@ -117,8 +125,11 @@
             return Ok(());
         }
 
+        #[cfg(not(target_os = "dragonfly"))]
         self.seal(fcntl::SealFlag::F_SEAL_GROW | fcntl::SealFlag::F_SEAL_SHRINK)
             .await?;
+        #[cfg(target_os = "dragonfly")]
+        self.seal().await?;
 
         self.add_mut_memmap()?;
 
@@ -128,6 +139,7 @@
     async fn final_seal(&mut self) -> Result<(), MemoryAllocationError> {
         self.mmap = None;
 
+        #[cfg(not(target_os = "dragonfly"))]
         self.seal(
             fcntl::SealFlag::F_SEAL_GROW
                 | fcntl::SealFlag::F_SEAL_SHRINK
@@ -135,6 +147,8 @@
                 | fcntl::SealFlag::F_SEAL_SEAL,
         )
         .await?;
+        #[cfg(target_os = "dragonfly")]
+        self.seal().await?;
 
         self.add_memmap()?;
 
@@ -178,6 +192,7 @@
 }
 
 impl SharedMemory {
+    #[cfg(not(target_os = "dragonfly"))]
     fn new_memfd(size: u64) -> std::io::Result<(OwnedFd, memmap::MmapMut)> {
         let memfd = nix::sys::memfd::memfd_create(
             c"glycin-frame",
@@ -192,6 +207,50 @@
         Ok((memfd, mmap))
     }
 
+    #[cfg(target_os = "dragonfly")]
+    fn new_memfd(size: u64) -> std::io::Result<(OwnedFd, memmap::MmapMut)> {
+        static NEXT_SHM_ID: AtomicU64 = AtomicU64::new(0);
+
+        let name = format!(
+            "/glycin-frame-{}-{}",
+            process::id(),
+            NEXT_SHM_ID.fetch_add(1, Ordering::Relaxed)
+        );
+        let name = CString::new(name).expect("generated shared-memory name has no NUL");
+        let raw_fd = unsafe {
+            libc::shm_open(
+                name.as_ptr(),
+                libc::O_CREAT | libc::O_EXCL | libc::O_RDWR,
+                0o600,
+            )
+        };
+        if raw_fd < 0 {
+            return Err(std::io::Error::last_os_error());
+        }
+
+        let unlink_result = unsafe { libc::shm_unlink(name.as_ptr()) };
+        if unlink_result < 0 {
+            let error = std::io::Error::last_os_error();
+            unsafe { libc::close(raw_fd) };
+            return Err(error);
+        }
+
+        let memfd = unsafe { OwnedFd::from_raw_fd(raw_fd) };
+        let size = libc::off_t::try_from(size).map_err(|_| {
+            std::io::Error::new(
+                std::io::ErrorKind::InvalidInput,
+                "required shared memory is too large",
+            )
+        })?;
+        if unsafe { libc::ftruncate(memfd.as_raw_fd(), size) } < 0 {
+            return Err(std::io::Error::last_os_error());
+        }
+
+        let mmap = unsafe { memmap::MmapMut::map_mut(memfd.as_raw_fd()) }?;
+
+        Ok((memfd, mmap))
+    }
+
     fn add_mut_memmap(&mut self) -> Result<(), MemoryAllocationError> {
         let mmap: memmap::MmapMut = unsafe { memmap::MmapMut::map_mut(&self.memfd) }
             .map_err(|err| MemoryAllocationError(err.to_string()))?;
@@ -210,6 +269,7 @@
         Ok(())
     }
 
+    #[cfg(not(target_os = "dragonfly"))]
     async fn seal(&self, seals: fcntl::SealFlag) -> Result<(), MemoryAllocationError> {
         let start = std::time::Instant::now();
 
@@ -233,6 +293,12 @@
             }
         }
 
+        Ok(())
+    }
+
+    #[cfg(target_os = "dragonfly")]
+    async fn seal(&self) -> Result<(), MemoryAllocationError> {
+        // DragonFly has no memfd seals; final_seal still replaces the mutable map with a read-only map.
         Ok(())
     }
 }
