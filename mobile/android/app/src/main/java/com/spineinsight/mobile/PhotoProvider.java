package com.spineinsight.mobile;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/** Grants a camera access to one explicitly selected private image. */
public final class PhotoProvider extends ContentProvider {
    @Override public boolean onCreate() { return true; }
    @Override public String getType(Uri uri) { return "image/jpeg"; }
    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        String name = uri.getLastPathSegment();
        if (name == null || !name.matches("[a-f0-9-]{36}\\.jpg")) throw new FileNotFoundException();
        File photo = new File(new File(getContext().getFilesDir(), "photos"), name);
        try { return ParcelFileDescriptor.open(photo, ParcelFileDescriptor.parseMode(mode)); }
        catch (IllegalArgumentException e) { throw new FileNotFoundException("Unsupported mode"); }
    }
    @Override public Cursor query(Uri u, String[] p, String s, String[] a, String o) {
        String name=u.getLastPathSegment(); if(name==null||!name.matches("[a-f0-9-]{36}\\.jpg"))return null;
        String[] columns=p==null?new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE}:p;
        Object[] values=new Object[columns.length]; File f=new File(new File(getContext().getFilesDir(),"photos"),name);
        for(int i=0;i<columns.length;i++){if(OpenableColumns.DISPLAY_NAME.equals(columns[i]))values[i]=name;else if(OpenableColumns.SIZE.equals(columns[i]))values[i]=f.exists()?f.length():0L;}
        MatrixCursor result=new MatrixCursor(columns); result.addRow(values); return result;
    }
    @Override public Uri insert(Uri u, ContentValues v) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri u, ContentValues v, String s, String[] a) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri u, String s, String[] a) { throw new UnsupportedOperationException(); }
}
