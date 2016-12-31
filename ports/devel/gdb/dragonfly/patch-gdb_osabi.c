--- gdb/osabi.c.orig	2016-08-01 15:50:20 UTC
+++ gdb/osabi.c
@@ -69,6 +69,7 @@ static const struct osabi_names gdb_osab
   { "NetBSD/a.out", NULL },
   { "NetBSD/ELF", NULL },
   { "OpenBSD/ELF", NULL },
+  { "DragonFly", NULL },
   { "WindowsCE", NULL },
   { "DJGPP", NULL },
   { "Irix", NULL },
@@ -497,6 +498,15 @@ generic_elf_osabi_sniff_abi_tag_sections
 	  return;
 	}
 
+      /* DragonFly.  */
+      if (check_note (abfd, sect, note, &sectsize, "DragonFly", 4,
+		      NT_DRAGONFLY_ABI_TAG))
+	{
+	  /* There is no need to check the version yet.  */
+	  *osabi = GDB_OSABI_DRAGONFLY;
+	  return;
+	}
+
       /* FreeBSD.  */
       if (check_note (abfd, sect, note, &sectsize, "FreeBSD", 4,
 		      NT_FREEBSD_ABI_TAG))
