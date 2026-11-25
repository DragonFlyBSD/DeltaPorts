--- discover/cpu_dragonfly.go.orig
+++ discover/cpu_dragonfly.go
@@ -0,0 +1,59 @@
+package discover
+
+/*
+#include <stdlib.h>
+#include <sys/types.h>
+#include <sys/sysctl.h>
+*/
+import "C"
+
+import (
+	"log/slog"
+	"syscall"
+	"unsafe"
+)
+
+// cpu_freebsd.go of the FreeBSD port: v_free_count is a u_long here, and
+// there is no vm.swap_total, so FreeSwap stays 0.
+
+func sysctlUint64(name string) (uint64, error) {
+	cname := C.CString(name)
+	defer C.free(unsafe.Pointer(cname))
+
+	var value C.uint64_t
+	size := C.size_t(unsafe.Sizeof(value))
+
+	ret := C.sysctlbyname(cname, unsafe.Pointer(&value), &size, nil, 0)
+	if ret != 0 {
+		return 0, syscall.Errno(ret)
+	}
+
+	return uint64(value), nil
+}
+
+func GetCPUMem() (memInfo, error) {
+	var mem memInfo
+
+	pageSize32, err := syscall.SysctlUint32("vm.stats.vm.v_page_size")
+	if err != nil {
+		return mem, err
+	}
+	pageSize := uint64(pageSize32)
+
+	physmem, err := sysctlUint64("hw.physmem")
+	if err != nil {
+		return mem, err
+	}
+
+	freeCount, err := sysctlUint64("vm.stats.vm.v_free_count")
+	if err != nil {
+		return mem, err
+	}
+
+	mem.TotalMemory = physmem
+	mem.FreeMemory = freeCount * pageSize
+
+	slog.Debug("GetCPUMem", "total_memory", mem.TotalMemory, "free_memory", mem.FreeMemory, "free_swap", mem.FreeSwap)
+
+	return mem, nil
+}
