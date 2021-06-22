--- vendor/github.com/docker/docker/pkg/system/stat_dragonfly.go.orig	2021-06-22 14:38:16 UTC
+++ vendor/github.com/docker/docker/pkg/system/stat_dragonfly.go
@@ -0,0 +1,13 @@
+package system // import "github.com/docker/docker/pkg/system"
+
+import "syscall"
+
+// fromStatT converts a syscall.Stat_t type to a system.Stat_t type
+func fromStatT(s *syscall.Stat_t) (*StatT, error) {
+	return &StatT{size: s.Size,
+		mode: uint32(s.Mode),
+		uid:  s.Uid,
+		gid:  s.Gid,
+		rdev: uint64(s.Rdev),
+		mtim: s.Mtim}, nil
+}
