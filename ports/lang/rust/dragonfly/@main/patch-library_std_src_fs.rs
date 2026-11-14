--- library/std/src/fs.rs.orig
+++ library/std/src/fs.rs
@@ -3767,6 +3767,12 @@
         let ancestors = path.ancestors();
         let mut uncreated_dirs = 0;
 
+        // DragonFly mkdir(".") and mkdir("..") return EISDIR rather than EEXIST.
+        let existing_directory = |err: &io::Error, path: &Path| {
+            matches!(err.kind(), io::ErrorKind::AlreadyExists | io::ErrorKind::IsADirectory)
+                && path.is_dir()
+        };
+
         for ancestor in ancestors {
             // for relative paths like "foo/bar", the parent of
             // "foo" will be "" which there's no need to invoke
@@ -3782,7 +3788,7 @@
                 //    - in case the path exists as a *file*
                 //    - and to avoid calls to .is_dir() in case of other errs
                 //      (i.e. PermissionDenied)
-                Err(e) if e.kind() == io::ErrorKind::AlreadyExists && ancestor.is_dir() => break,
+                Err(e) if existing_directory(&e, ancestor) => break,
                 Err(e) => return Err(e),
             }
         }
@@ -3793,7 +3799,7 @@
 
         for uncreated_dir in uncreated_dirs_vec.iter().rev() {
             if let Err(e) = self.inner.mkdir(uncreated_dir) {
-                if e.kind() != io::ErrorKind::AlreadyExists || !uncreated_dir.is_dir() {
+                if !existing_directory(&e, uncreated_dir) {
                     return Err(e);
                 }
             }
