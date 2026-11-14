File::lock and friends: DragonFly has flock(2). Unsupported here made cargo
skip its build-directory lock, so concurrent cargo runs raced.

--- library/std/src/sys/fs/unix.rs.orig
+++ library/std/src/sys/fs/unix.rs
@@ -1323,6 +1323,7 @@
     pub fn lock(&self) -> io::Result<()> {
         cfg_select! {
             any(
+                target_os = "dragonfly",
                 target_os = "freebsd",
                 target_os = "fuchsia",
                 target_os = "hurd",
@@ -1346,6 +1347,7 @@
     pub fn lock_shared(&self) -> io::Result<()> {
         cfg_select! {
             any(
+                target_os = "dragonfly",
                 target_os = "freebsd",
                 target_os = "fuchsia",
                 target_os = "hurd",
@@ -1369,6 +1371,7 @@
     pub fn try_lock(&self) -> Result<(), TryLockError> {
         cfg_select! {
             any(
+                target_os = "dragonfly",
                 target_os = "freebsd",
                 target_os = "fuchsia",
                 target_os = "hurd",
@@ -1404,6 +1407,7 @@
     pub fn try_lock_shared(&self) -> Result<(), TryLockError> {
         cfg_select! {
             any(
+                target_os = "dragonfly",
                 target_os = "freebsd",
                 target_os = "fuchsia",
                 target_os = "hurd",
@@ -1439,6 +1443,7 @@
     pub fn unlock(&self) -> io::Result<()> {
         cfg_select! {
             any(
+                target_os = "dragonfly",
                 target_os = "freebsd",
                 target_os = "fuchsia",
                 target_os = "hurd",
