--- libpkg/pkg_elf.c.orig	2026-11-12 22:54:42 UTC
+++ libpkg/pkg_elf.c
@@ -56,6 +56,33 @@
 
 #ifndef HAVE_ELF_NOTE
 typedef Elf32_Nhdr Elf_Note;
+#endif
+
+#ifdef __DragonFly__
+/*
+ * A note-less ELF file that needs glibc's libc.so.6 targets Linux: native
+ * DragonFly objects need libc.so.8.
+ */
+static bool
+elf_needs_glibc(Elf *e, Elf_Scn *dynamic, size_t numdyn, size_t sh_link)
+{
+	Elf_Data *data = elf_getdata(dynamic, NULL);
+
+	if (data == NULL)
+		return (false);
+	for (size_t i = 0; i < numdyn; i++) {
+		GElf_Dyn *dyn, dyn_mem;
+
+		if ((dyn = gelf_getdyn(data, i, &dyn_mem)) == NULL)
+			return (false);
+		if (dyn->d_tag != DT_NEEDED)
+			continue;
+		const char *lib = elf_strptr(e, sh_link, dyn->d_un.d_val);
+		if (lib != NULL && strcmp(lib, "libc.so.6") == 0)
+			return (true);
+	}
+	return (false);
+}
 #endif
 
 static int
@@ -189,6 +216,13 @@
 		 */
 		if (elfhdr.e_ident[EI_OSABI] == ELFOSABI_FREEBSD) {
 			elf_abi.os = PKG_OS_FREEBSD;
+#ifdef __DragonFly__
+		} else if (ctx.abi.os == PKG_OS_DRAGONFLY &&
+		    elfhdr.e_ident[EI_OSABI] == ELFOSABI_NONE &&
+		    !elf_needs_glibc(e, dynamic, numdyn, sh_link)) {
+			/* Native DragonFly shared libraries need not carry ABI notes. */
+			elf_abi.os = PKG_OS_DRAGONFLY;
+#endif
 		} else if (ctx.abi.os == PKG_OS_LINUX || ctx.abi.os == PKG_OS_FREEBSD) {
 			/* There is no reliable way to identify shared libraries targeting Linux.
 			 * It would be possible to reliably identify Linux executables by checking
