--- internal/restic/node_dragonfly.go.orig	1970-01-01 01:00:00.000000000 +0100
+++ internal/restic/node_dragonfly.go	2021-06-13 15:23:09.253146000 +0200
@@ -0,0 +1,34 @@
+// +build dragonfly
+
+package restic
+
+import "syscall"
+
+func (node Node) restoreSymlinkTimestamps(path string, utimes [2]syscall.Timespec) error {
+        return nil
+}
+
+func (node Node) device() int {
+        return int(node.Device)
+}
+
+func (s statT) atim() syscall.Timespec { return s.Atim }
+func (s statT) mtim() syscall.Timespec { return s.Mtim }
+func (s statT) ctim() syscall.Timespec { return s.Ctim }
+
+// Getxattr retrieves extended attribute data associated with path.
+func Getxattr(path, name string) ([]byte, error) {
+        return nil, nil
+}
+
+// Listxattr retrieves a list of names of extended attributes associated with the
+// given path in the file system.
+func Listxattr(path string) ([]string, error) {
+        return nil, nil
+}
+
+// Setxattr associates name and data together as an attribute of path.
+func Setxattr(path, name string, data []byte) error {
+        return nil
+}
+
