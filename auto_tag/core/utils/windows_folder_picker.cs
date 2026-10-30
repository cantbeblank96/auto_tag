// Native Common Item Dialog interop for Windows PowerShell / .NET Framework.
// Keep C# 5 syntax so the Windows-provided PowerShell compiler can build it.
using System;
using System.IO;
using System.Runtime.InteropServices;

namespace AutoTag
{
    public static class WindowsFolderPicker
    {
        // Stable auto_tag identity: do not share PowerShell.exe's default history.
        private static readonly Guid ClientGuid = new Guid("a80b3a85-2d09-4fd8-a86e-bb680c9cc3b9");
        private const uint FOS_NOCHANGEDIR = 0x8;
        private const uint FOS_PICKFOLDERS = 0x20;
        private const uint FOS_FORCEFILESYSTEM = 0x40;
        private const uint FOS_PATHMUSTEXIST = 0x800;
        private const uint SIGDN_FILESYSPATH = 0x80058000;
        private const int ERROR_CANCELLED = unchecked((int)0x800704C7);

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern int SHCreateItemFromParsingName(
            [MarshalAs(UnmanagedType.LPWStr)] string path,
            IntPtr bindContext,
            ref Guid interfaceId,
            [MarshalAs(UnmanagedType.Interface)] out IShellItem item);

        public static string Pick(string title, string initialDirectory, IntPtr owner)
        {
            IFileOpenDialog dialog = null;
            IShellItem initialItem = null;
            IShellItem result = null;
            IntPtr pathPointer = IntPtr.Zero;
            try
            {
                dialog = (IFileOpenDialog)new FileOpenDialog();
                Guid clientGuid = ClientGuid;
                Marshal.ThrowExceptionForHR(dialog.SetClientGuid(ref clientGuid));

                uint options;
                Marshal.ThrowExceptionForHR(dialog.GetOptions(out options));
                Marshal.ThrowExceptionForHR(dialog.SetOptions(options | FOS_PICKFOLDERS |
                    FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR));
                Marshal.ThrowExceptionForHR(dialog.SetTitle(title));
                Marshal.ThrowExceptionForHR(dialog.SetOkButtonLabel("选择文件夹"));

                if (!String.IsNullOrEmpty(initialDirectory) && Directory.Exists(initialDirectory))
                {
                    Guid shellItemId = typeof(IShellItem).GUID;
                    int hr = SHCreateItemFromParsingName(initialDirectory, IntPtr.Zero,
                        ref shellItemId, out initialItem);
                    if (hr >= 0 && initialItem != null)
                    {
                        // SetDefaultFolder only applies when no saved location exists.
                        // An unavailable initial folder must not block opening the dialog.
                        dialog.SetDefaultFolder(initialItem);
                    }
                }

                int showResult = dialog.Show(owner);
                if (showResult == ERROR_CANCELLED)
                    return null;
                Marshal.ThrowExceptionForHR(showResult);
                Marshal.ThrowExceptionForHR(dialog.GetResult(out result));
                Marshal.ThrowExceptionForHR(result.GetDisplayName(SIGDN_FILESYSPATH, out pathPointer));
                string path = Marshal.PtrToStringUni(pathPointer);
                if (String.IsNullOrWhiteSpace(path))
                    throw new InvalidOperationException("目录窗口未返回有效路径");
                return path;
            }
            finally
            {
                if (pathPointer != IntPtr.Zero)
                    Marshal.FreeCoTaskMem(pathPointer);
                if (result != null)
                    Marshal.ReleaseComObject(result);
                if (initialItem != null)
                    Marshal.ReleaseComObject(initialItem);
                if (dialog != null)
                    Marshal.ReleaseComObject(dialog);
            }
        }
    }

    [ComImport]
    [Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
    public class FileOpenDialog { }

    // Include inherited IModalWindow/IFileDialog slots in their native vtable order.
    [ComImport]
    [Guid("D57C7288-D4AD-4768-BE02-9D969532D960")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IFileOpenDialog
    {
        [PreserveSig] int Show(IntPtr owner);
        [PreserveSig] int SetFileTypes(uint count, IntPtr filters);
        [PreserveSig] int SetFileTypeIndex(uint index);
        [PreserveSig] int GetFileTypeIndex(out uint index);
        [PreserveSig] int Advise(IntPtr events, out uint cookie);
        [PreserveSig] int Unadvise(uint cookie);
        [PreserveSig] int SetOptions(uint options);
        [PreserveSig] int GetOptions(out uint options);
        [PreserveSig] int SetDefaultFolder([MarshalAs(UnmanagedType.Interface)] IShellItem folder);
        [PreserveSig] int SetFolder([MarshalAs(UnmanagedType.Interface)] IShellItem folder);
        [PreserveSig] int GetFolder(out IShellItem folder);
        [PreserveSig] int GetCurrentSelection(out IShellItem item);
        [PreserveSig] int SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        [PreserveSig] int GetFileName(out IntPtr name);
        [PreserveSig] int SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        [PreserveSig] int SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        [PreserveSig] int SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        [PreserveSig] int GetResult(out IShellItem result);
        [PreserveSig] int AddPlace([MarshalAs(UnmanagedType.Interface)] IShellItem item, int placement);
        [PreserveSig] int SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
        [PreserveSig] int Close(int result);
        [PreserveSig] int SetClientGuid(ref Guid guid);
        [PreserveSig] int ClearClientData();
        [PreserveSig] int SetFilter(IntPtr filter);
        [PreserveSig] int GetResults(out IntPtr items);
        [PreserveSig] int GetSelectedItems(out IntPtr items);
    }

    [ComImport]
    [Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IShellItem
    {
        [PreserveSig] int BindToHandler(IntPtr context, ref Guid handler, ref Guid interfaceId, out IntPtr result);
        [PreserveSig] int GetParent(out IShellItem parent);
        [PreserveSig] int GetDisplayName(uint nameType, out IntPtr name);
        [PreserveSig] int GetAttributes(uint mask, out uint attributes);
        [PreserveSig] int Compare([MarshalAs(UnmanagedType.Interface)] IShellItem other, uint hint, out int order);
    }
}
