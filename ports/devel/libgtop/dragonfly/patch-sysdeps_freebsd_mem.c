--- sysdeps/freebsd/mem.c.orig
+++ sysdeps/freebsd/mem.c
@@ -93,7 +93,11 @@
 	memtotal = mem_get_by_bytes (server, "hw.physmem");
 	memactive = mem_get_by_pages (server, "vm.stats.vm.v_active_count");
 	meminactive = mem_get_by_pages (server, "vm.stats.vm.v_inactive_count");
+#ifdef __DragonFly__
+	memlaundry = 0;
+#else
 	memlaundry = mem_get_by_pages (server, "vm.stats.vm.v_laundry_count");
+#endif
 	memwired = mem_get_by_pages (server, "vm.stats.vm.v_wire_count");
 	memcached = mem_get_by_pages (server, "vm.stats.vm.v_cache_count");
 	membuffer = mem_get_by_bytes (server, "vfs.bufspace");
