--- command/agent/host/dragonfly.go.orig	2021-06-22 14:22:13 UTC
+++ command/agent/host/dragonfly.go
@@ -0,0 +1,50 @@
+// +build dragonfly
+
+package host
+
+import (
+	"syscall"
+)
+
+func IntToString(orig []int8) string {
+	ret := make([]byte, len(orig))
+	size := -1
+	for i, o := range orig {
+		if o == 0 {
+			size = i
+			break
+		}
+		ret[i] = byte(o)
+	}
+	if size == -1 {
+		size = len(orig)
+	}
+
+	return string(ret[0:size])
+}
+
+// mountedPaths produces a list of mounts
+func mountedPaths() []string {
+	var paths []string
+
+	n, err := syscall.Getfsstat(nil, 1)
+	if err != nil {
+		return []string{err.Error()}
+	}
+
+	data := make([]syscall.Statfs_t, n)
+	_, err = syscall.Getfsstat(data, 1)
+	if err != nil {
+		return []string{err.Error()}
+	}
+
+	empty := syscall.Statfs_t{}
+	for _, stat := range data {
+		if stat == empty {
+			return []string{"empy stat struct returned"}
+		}
+		paths = append(paths, IntToString(stat.Mntonname[:]))
+	}
+
+	return paths
+}
