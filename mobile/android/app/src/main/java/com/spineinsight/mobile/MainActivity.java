package com.spineinsight.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.ClipData;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.*;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.UUID;

public final class MainActivity extends Activity {
    private static final int CAMERA = 71, PICK = 72, EXPORT = 73, EXPORT_IMAGE = 74;
    private static final int BG = Color.rgb(248,247,252), INK = Color.rgb(38,35,56), MUTED = Color.rgb(107,102,126), PURPLE = Color.rgb(94,106,210), SOFT = Color.rgb(240,236,252);
    private final String[] views = {"正面", "侧面", "背面", "斜侧面"};
    private LinearLayout content, root, navigation;
    private JSONArray records = new JSONArray(), draft = new JSONArray();
    private JSONArray originalPhotos = new JSONArray();
    private int editingIndex = -1, detailIndex = -1;
    private String page = "home", note = "", angle = "正面", pending = "", pendingExportImage = "";
    private boolean storageReady = true;
    private volatile boolean exporting = false;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        if (!getPreferences(MODE_PRIVATE).getBoolean("privacyAccepted",false)) {
            new AlertDialog.Builder(this).setTitle("隐私说明")
                .setMessage("本应用由刘思辰开发。你主动填写的问卷、输入的文字、拍摄和选择的图片由应用保存在本机，用于展示和管理记录。本应用本身不直接联网；你可主动在系统浏览器中打开自己设置的网页服务，网页中的账号和数据由对应网站及服务器处理，本机照片不会自动上传。\n\n你可以在应用内删除记录；卸载应用或清除数据会删除应用内保存的内容。使用系统相机和图片选择器时，相关系统应用的处理行为以其说明为准。\n\n联系邮箱：benjaminmcree@qq.com")
                .setCancelable(false).setNegativeButton("不同意",(d,w)->finish())
                .setPositiveButton("同意",(d,w)->{if(getPreferences(MODE_PRIVATE).edit().putBoolean("privacyAccepted",true).commit())initialize(state);else {toast("设置保存失败");finish();}}).show();
        } else initialize(state);
    }
    private void initialize(Bundle state) {
        new File(getFilesDir(), "photos").mkdirs();
        try { records = new JSONArray(getPreferences(MODE_PRIVATE).getString("records", "[]")); }
        catch (JSONException e) { storageReady = false; }
        if (state != null) {
            page = state.getString("page", "home"); note = state.getString("note", "");
            angle = state.getString("angle", "正面"); pending = state.getString("pending", ""); pendingExportImage=state.getString("pendingExportImage", "");
            try { draft = new JSONArray(state.getString("draft", "[]")); } catch (JSONException ignored) { draft = new JSONArray(); }
            try { originalPhotos = new JSONArray(state.getString("originalPhotos", "[]")); } catch (JSONException ignored) { originalPhotos = new JSONArray(); }
            editingIndex = state.getInt("editingIndex", -1);
            detailIndex = state.getInt("detailIndex", -1);
        }
        render();
        if (!storageReady) new AlertDialog.Builder(this).setTitle("记录暂时无法读取").setMessage("为避免覆盖原有数据，保存功能已暂停。请联系支持邮箱 benjaminmcree@qq.com。") .setPositiveButton("知道了", null).show();
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out); out.putString("page",page); out.putString("note",note); out.putString("angle",angle); out.putString("pending",pending); out.putString("pendingExportImage",pendingExportImage); out.putString("draft",draft.toString()); out.putString("originalPhotos",originalPhotos.toString()); out.putInt("editingIndex",editingIndex); out.putInt("detailIndex",detailIndex);
    }

    private int dp(float n) { return (int)(n * getResources().getDisplayMetrics().density + .5f); }
    private GradientDrawable shape(int color, float radius) { GradientDrawable d = new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(radius)); return d; }
    private TextView text(String s, int size, int color, boolean bold) {
        TextView t = new TextView(this); t.setText(s); t.setTextColor(color); t.setTextSize(size); t.setLineSpacing(dp(3),1f);
        if (bold) t.setTypeface(Typeface.DEFAULT, Typeface.BOLD); return t;
    }
    private LinearLayout vertical() { LinearLayout l = new LinearLayout(this); l.setOrientation(LinearLayout.VERTICAL); return l; }
    private void gap(LinearLayout p, int h) { View v = new View(this); p.addView(v,new LinearLayout.LayoutParams(1,dp(h))); }
    private void add(LinearLayout p, View child) { p.addView(child,new LinearLayout.LayoutParams(-1,-2)); }
    private TextView button(String title, boolean primary, Runnable action) {
        TextView b = text(title, 15, primary ? Color.WHITE : PURPLE, true); b.setGravity(Gravity.CENTER); b.setPadding(dp(16),dp(15),dp(16),dp(15)); b.setBackground(shape(primary ? PURPLE : SOFT, 18));
        b.setOnClickListener(v -> action.run()); return b;
    }
    private LinearLayout card() { LinearLayout c = vertical(); c.setPadding(dp(20),dp(19),dp(20),dp(19)); c.setBackground(shape(Color.WHITE,22)); return c; }
    private void section(String title, String sub) { add(content,text(title,21,INK,true)); if (!sub.isEmpty()) { gap(content,4); add(content,text(sub,13,MUTED,false)); } gap(content,12); }
    private void go(String p) { page = p; render(); }

    private void render() {
        root = vertical(); root.setBackgroundColor(BG); setContentView(root);
        // Android 15 enforces edge-to-edge for apps targeting API 35.
        if (android.os.Build.VERSION.SDK_INT >= 35) {
            root.setOnApplyWindowInsetsListener((v, insets) -> {
                android.graphics.Insets bars = insets.getInsets(android.view.WindowInsets.Type.systemBars());
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return insets;
            });
            root.requestApplyInsets();
        }
        LinearLayout header = new LinearLayout(this); header.setGravity(Gravity.CENTER_VERTICAL); header.setPadding(dp(21),dp(14),dp(21),dp(12));
        ImageView logo = new ImageView(this); logo.setImageResource(com.spineinsight.mobile.R.drawable.brand_logo); header.addView(logo,new LinearLayout.LayoutParams(dp(39),dp(39)));
        LinearLayout brand = vertical(); brand.setPadding(dp(10),0,0,0); add(brand,text("脊见智诊",19,INK,true)); add(brand,text("SPINEINSIGHT  ·  本机版",10,MUTED,false)); header.addView(brand,new LinearLayout.LayoutParams(0,-2,1));
        TextView badge = text("本机保存",11,PURPLE,true); badge.setPadding(dp(10),dp(6),dp(10),dp(6)); badge.setBackground(shape(SOFT,20)); header.addView(badge); add(root,header);
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); content = vertical(); content.setPadding(dp(20),dp(8),dp(20),dp(26)); scroll.addView(content); root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        switch (page) { case "records": recordsPage(); break; case "detail": detailPage(detailIndex); break; case "editor": editor(); break; case "guide": guide(); break; case "settings": settings(); break; default: home(); }
        if (!page.equals("editor") && !page.equals("guide")) {
            navigation = new LinearLayout(this); navigation.setPadding(dp(14),dp(8),dp(14),dp(12)); navigation.setBackgroundColor(Color.WHITE);
            nav("首页", "home"); nav("档案", "records"); nav("设置", "settings"); add(root,navigation);
        }
    }
    private void nav(String label, String target) { TextView n = text(label,14,page.equals(target)?PURPLE:MUTED,true); n.setGravity(Gravity.CENTER); n.setPadding(0,dp(14),0,dp(14)); n.setOnClickListener(v->go(target)); navigation.addView(n,new LinearLayout.LayoutParams(0,-2,1)); }
    private void home() {
        gap(content,12); add(content,text("YOUR HEALTH SPACE",11,PURPLE,true)); gap(content,10); add(content,text("看见每一次变化。\n从记录开始。",29,INK,true)); gap(content,10);
        add(content,text("把照片与日常观察整理成自己的档案。",14,MUTED,false)); gap(content,22);
        LinearLayout hero = card(); add(hero,text("体表照片",20,INK,true)); gap(hero,5); add(hero,text("拍摄前先选择角度，按照引导调整位置。",13,MUTED,false)); gap(hero,18); add(hero,button("开始引导拍摄  →",true,()->{newDraft();go("guide");})); add(content,hero);
        gap(content,18); section("健康问卷","从网页端问卷整理而来，回答保存在本机"); LinearLayout questionnaire=card(); add(questionnaire,text("了解自己的日常情况",16,INK,true)); gap(questionnaire,6); add(questionnaire,text("填写问卷并回看已保存的回答；包含固定安全提示，当前版本不提供评分或综合分析。",13,MUTED,false)); gap(questionnaire,12); add(questionnaire,button("填写或查看问卷",false,()->OfflineQuestionnaire.launch(this))); add(content,questionnaire); gap(content,18); section("我的档案","所有内容保存在当前设备");
        LinearLayout stats=card(); add(stats,text(String.valueOf(records.length()),27,PURPLE,true)); add(stats,text("条本地记录",13,MUTED,false)); gap(stats,14); add(stats,button("查看全部记录",false,()->go("records"))); add(content,stats);
        gap(content,18); LinearLayout web=card(); add(web,text("网页工作台",16,INK,true)); gap(web,6); add(web,text("设置你自己的网页服务地址，在浏览器中访问网页端。",13,MUTED,false)); gap(web,12); add(web,button("打开网页端",false,this::openWebsite)); add(content,web); gap(content,18); section("探索工作区","与网页端一致的功能方向");
        LinearLayout work=card(); add(work,text("影像分析  ·  步态评估  ·  报告",16,INK,true)); gap(work,6); add(work,text("这些功能需要在配置并打开的网页端使用，本机应用暂未内置分析和报告。",13,MUTED,false)); add(content,work);
    }
    private void newDraft() { note=""; angle="正面"; pending=""; draft=new JSONArray(); originalPhotos=new JSONArray(); editingIndex=-1; }
    private boolean containsFile(JSONArray items,String name){for(int i=0;i<items.length();i++){JSONObject p=items.optJSONObject(i);if(p!=null&&name.equals(p.optString("file")))return true;}return false;}
    private File photoFile(String name) { return new File(new File(getFilesDir(),"photos"),name); }
    private void recordsPage() {
        section("我的档案", "按时间保存照片和文字记录"); add(content,button("＋  新建记录",true,()->{newDraft();go("editor");})); gap(content,18);
        if (records.length()==0) { LinearLayout e=card(); add(e,text("还没有记录",17,INK,true)); gap(e,7); add(e,text("拍摄或选择图片，开始建立你的第一条记录。",13,MUTED,false)); add(content,e); return; }
        for(int i=records.length()-1;i>=0;i--) { final int pos=i; JSONObject r=records.optJSONObject(i); if(r==null) continue;
            LinearLayout c=card(); add(c,text(r.optString("date", "记录"),16,INK,true)); gap(c,6);
            String n=r.optString("note", ""); add(c,text(n.isEmpty()?"图片记录":n,14,MUTED,false)); JSONArray photos=r.optJSONArray("photos"); gap(c,6); add(c,text((photos==null?0:photos.length())+" 张图片",12,PURPLE,true));
            gap(c,12); add(c,button("查看记录",false,()->showRecord(pos))); add(content,c); gap(content,12);
        }
    }
    private void showRecord(int pos) { if(records.optJSONObject(pos)==null)return; detailIndex=pos;page="detail";render(); }
    private void detailPage(int pos) { JSONObject r=records.optJSONObject(pos); if(r==null){recordsPage();return;} section(r.optString("date"),"本机记录");
        LinearLayout c=card(); add(c,text(r.optString("note").isEmpty()?"无备注":r.optString("note"),15,INK,false)); JSONArray a=r.optJSONArray("photos");
        if(a!=null) for(int j=0;j<a.length();j++){ JSONObject p=a.optJSONObject(j); if(p==null)continue; gap(c,14); add(c,text(p.optString("angle","图片"),13,PURPLE,true)); ImageView image=new ImageView(this); image.setAdjustViewBounds(true); image.setMaxHeight(dp(340)); image.setScaleType(ImageView.ScaleType.FIT_CENTER); String name=p.optString("file"); File f=photoFile(name); image.setImageBitmap(loadPreview(f)); c.addView(image,new LinearLayout.LayoutParams(-1,dp(220))); gap(c,8); add(c,button("导出此图片",false,()->chooseImageExport(name))); }
        add(content,c); gap(content,16); add(content,button("编辑记录",true,()->editRecord(pos))); gap(content,10); add(content,button("删除记录",false,()->new AlertDialog.Builder(this).setTitle("删除这条记录？").setMessage("记录和应用内保存的图片副本将被删除。") .setNegativeButton("取消",null).setPositiveButton("删除",(d,w)->deleteRecord(pos)).show())); gap(content,10); add(content,button("返回档案",false,()->go("records")));
    }
    private void editRecord(int i){JSONObject r=records.optJSONObject(i);if(r==null)return;newDraft();editingIndex=i;note=r.optString("note","");JSONArray p=r.optJSONArray("photos");try{originalPhotos=new JSONArray(p==null?"[]":p.toString());draft=new JSONArray(originalPhotos.toString());}catch(JSONException ignored){return;}go("editor");}
    private boolean persist(JSONArray a) { if(exporting){toast("正在导出，请稍后再保存或删除记录");return false;} if(!storageReady)return false; boolean saved=getPreferences(MODE_PRIVATE).edit().putString("records",a.toString()).commit(); if(!saved){storageReady=false;toast("保存状态不确定，请关闭应用后重新打开；照片副本已保留");} return saved; }
    private void deleteRecord(int i) { JSONArray copy=new JSONArray(); JSONObject removed=records.optJSONObject(i); for(int j=0;j<records.length();j++)if(j!=i)copy.put(records.opt(j));
        if(!persist(copy)){toast("保存失败，原记录已保留");return;} records=copy; JSONArray p=removed==null?null:removed.optJSONArray("photos"); if(p!=null)for(int j=0;j<p.length();j++){JSONObject x=p.optJSONObject(j);if(x!=null)photoFile(x.optString("file")).delete();} go("records"); }
    private void editor() {
        section(editingIndex<0?"新建记录":"编辑记录","记录照片与想要保留的文字"); LinearLayout c=card(); add(c,text("备注",15,INK,true)); gap(c,8);
        EditText edit=new EditText(this); edit.setSingleLine(false); edit.setMinLines(3); edit.setTextSize(15); edit.setTextColor(INK); edit.setHint("写下本次观察或备注（选填）"); edit.setText(note); edit.setPadding(dp(12),dp(10),dp(12),dp(10)); edit.setBackground(shape(BG,12));
        edit.addTextChangedListener(new TextWatcher(){public void beforeTextChanged(CharSequence s,int st,int count,int after){} public void onTextChanged(CharSequence s,int st,int before,int count){note=s.toString();} public void afterTextChanged(Editable e){} }); add(c,edit);
        gap(c,18); add(c,text("照片  "+draft.length()+" / 4",15,INK,true)); gap(c,6); add(c,text("可拍摄或从相册选择，图片保存在应用中。",12,MUTED,false));
        for(int i=0;i<draft.length();i++){ final int pos=i; JSONObject p=draft.optJSONObject(i); if(p!=null){gap(c,8); add(c,button(p.optString("angle")+" · 点击移除",false,()->removeDraftPhoto(pos)));} }
        gap(c,14); add(c,button("引导拍摄",false,()->go("guide"))); gap(c,8); add(c,button("从相册选择图片",false,this::pick)); add(content,c);
        gap(content,18); add(content,button("保存记录",true,this::save)); gap(content,10); add(content,button("放弃并返回",false,this::discard));
    }
    private void guide() {
        section("拍摄引导","选择视角，准备好后打开系统相机"); LinearLayout c=card(); add(c,text("选择拍摄视角",16,INK,true)); gap(c,12);
        for(String v:views){add(c,button((v.equals(angle)?"●  ":"○  ")+v,false,()->{angle=v;go("guide");}));gap(c,7);}
        gap(c,8); add(c,text("拍摄前建议",16,INK,true)); gap(c,8); add(c,text("• 选择光线均匀、背景简洁的位置\n• 让身体自然站立，确保目标区域位于画面中\n• 可请可信任的人协助拍摄，拍摄完成后自行确认",13,MUTED,false));
        gap(c,16); add(c,button("打开相机 · "+angle,true,this::capture)); add(content,c); gap(content,12); add(content,button("返回记录",false,()->go("editor")));
    }
    private void capture() { if(draft.length()>=4){toast("每条记录最多 4 张图片");return;} pending=UUID.randomUUID()+".jpg"; Uri uri=new Uri.Builder().scheme("content").authority(getPackageName()+".photos").appendPath(pending).build();
        Intent i=new Intent(MediaStore.ACTION_IMAGE_CAPTURE); i.putExtra(MediaStore.EXTRA_OUTPUT,uri); i.setClipData(ClipData.newRawUri("photo",uri)); i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        try{startActivityForResult(i,CAMERA);}catch(Exception e){photoFile(pending).delete();pending="";toast("无法打开系统相机");} }
    private void pick() {if(draft.length()>=4){toast("每条记录最多 4 张图片");return;} Intent i=new Intent(Intent.ACTION_OPEN_DOCUMENT); i.addCategory(Intent.CATEGORY_OPENABLE); i.setType("image/*"); try{startActivityForResult(i,PICK);}catch(Exception e){toast("无法打开图片选择器");}}
    @Override protected void onActivityResult(int request,int result,Intent data) {super.onActivityResult(request,result,data);
        if(request==CAMERA){File f=photoFile(pending); if(result==RESULT_OK && !pending.isEmpty() && f.isFile() && f.length()>0 && f.length()<=20L*1024*1024 && validImage(f)){String normalized=normalizeImageFile(pending);if(normalized!=null)addPhoto(normalized,angle);else {f.delete();toast("无法保存图片");}} else {if(!pending.isEmpty())f.delete(); if(result==RESULT_OK)toast("图片不可用或超过 20 MB");} pending=""; go("editor");}
        if(request==PICK && result==RESULT_OK && data!=null && data.getData()!=null){String name=UUID.randomUUID()+".jpg"; File out=photoFile(name); try(InputStream in=getContentResolver().openInputStream(data.getData()); FileOutputStream os=new FileOutputStream(out)){if(in==null)throw new Exception(); byte[] b=new byte[16384];int n;long total=0;while((n=in.read(b))!=-1){total+=n;if(total>20L*1024*1024)throw new Exception();os.write(b,0,n);} if(total==0)throw new Exception();}catch(Exception e){out.delete();toast("图片导入失败或超过 20 MB");go("editor");return;} if(!validImage(out)){out.delete();toast("无法识别该图片");}else {String normalized=normalizeImageFile(name);if(normalized!=null)addPhoto(normalized,"相册图片");else {out.delete();toast("无法保存图片");}}go("editor");}
        if(request==EXPORT && result==RESULT_OK && data!=null && data.getData()!=null)exportBackup(data.getData());
        if(request==EXPORT_IMAGE){String file=pendingExportImage;pendingExportImage="";if(result==RESULT_OK && data!=null && data.getData()!=null)exportImage(file,data.getData());}
    }
    private boolean validImage(File f){BitmapFactory.Options o=new BitmapFactory.Options();o.inJustDecodeBounds=true;BitmapFactory.decodeFile(f.getAbsolutePath(),o);return o.outWidth>0&&o.outHeight>0&&o.outWidth<=16000&&o.outHeight<=16000&&(long)o.outWidth*o.outHeight<=64000000L;}
    private String normalizeImageFile(String temporaryName){
        if(!temporaryName.matches("[a-f0-9-]{36}\\.jpg"))return null;
        File source=photoFile(temporaryName);BitmapFactory.Options probe=new BitmapFactory.Options();probe.inJustDecodeBounds=true;BitmapFactory.decodeFile(source.getAbsolutePath(),probe);
        String mime=probe.outMimeType==null?"":probe.outMimeType.toLowerCase(Locale.ROOT), ext;
        switch(mime){case "image/jpeg":ext="jpg";break;case "image/png":ext="png";break;case "image/webp":ext="webp";break;case "image/gif":ext="gif";break;case "image/bmp":ext="bmp";break;case "image/heif":ext="heif";break;case "image/heic":ext="heic";break;case "image/avif":ext="avif";break;default:ext="img";}
        String name=temporaryName.substring(0,36)+"."+ext;if(name.equals(temporaryName))return name;
        return source.renameTo(photoFile(name))?name:null;
    }
    private android.graphics.Bitmap loadPreview(File file){
        BitmapFactory.Options bounds=new BitmapFactory.Options(); bounds.inJustDecodeBounds=true; BitmapFactory.decodeFile(file.getAbsolutePath(),bounds);
        if(bounds.outWidth<=0||bounds.outHeight<=0)return null;
        BitmapFactory.Options opts=new BitmapFactory.Options(); opts.inSampleSize=1;
        while(bounds.outWidth/opts.inSampleSize>1200||bounds.outHeight/opts.inSampleSize>1200)opts.inSampleSize*=2;
        android.graphics.Bitmap image=BitmapFactory.decodeFile(file.getAbsolutePath(),opts); if(image==null)return null;
        try {
            int orientation=new android.media.ExifInterface(file.getAbsolutePath()).getAttributeInt(android.media.ExifInterface.TAG_ORIENTATION,android.media.ExifInterface.ORIENTATION_NORMAL);
            android.graphics.Matrix transform=new android.graphics.Matrix();
            switch(orientation){
                case android.media.ExifInterface.ORIENTATION_ROTATE_90: transform.setRotate(90); break;
                case android.media.ExifInterface.ORIENTATION_ROTATE_180: transform.setRotate(180); break;
                case android.media.ExifInterface.ORIENTATION_ROTATE_270: transform.setRotate(270); break;
                case android.media.ExifInterface.ORIENTATION_FLIP_HORIZONTAL: transform.setScale(-1,1); break;
                case android.media.ExifInterface.ORIENTATION_FLIP_VERTICAL: transform.setScale(1,-1); break;
                case android.media.ExifInterface.ORIENTATION_TRANSPOSE: transform.setRotate(90); transform.postScale(-1,1); break;
                case android.media.ExifInterface.ORIENTATION_TRANSVERSE: transform.setRotate(270); transform.postScale(-1,1); break;
                default: return image;
            }
            android.graphics.Bitmap rotated=android.graphics.Bitmap.createBitmap(image,0,0,image.getWidth(),image.getHeight(),transform,true);
            if(rotated!=image)image.recycle(); return rotated;
        } catch (java.io.IOException | IllegalArgumentException e){return image;}
    }
    private void addPhoto(String name,String label){try{JSONObject p=new JSONObject();p.put("file",name);p.put("angle",label);draft.put(p);}catch(JSONException ignored){photoFile(name).delete();}}
    private void removeDraftPhoto(int index){if(!storageReady){toast("请关闭应用后重新打开");return;}JSONObject p=draft.optJSONObject(index);if(p==null)return;String file=p.optString("file");draft.remove(index);if(!containsFile(originalPhotos,file))photoFile(file).delete();go("editor");}
    private void save() {if(note.trim().isEmpty()&&draft.length()==0){toast("请先输入备注或添加图片");return;} try{JSONArray next=new JSONArray(records.toString());JSONObject old=editingIndex<0?null:next.optJSONObject(editingIndex);if(editingIndex>=0&&old==null){toast("原记录不存在，保存已停止");return;}JSONObject r=new JSONObject();r.put("id",old==null?UUID.randomUUID().toString():old.optString("id"));r.put("date",old==null?new SimpleDateFormat("yyyy-MM-dd HH:mm",Locale.CHINA).format(new Date()):old.optString("date"));r.put("note",note.trim());r.put("photos",new JSONArray(draft.toString()));if(old==null)next.put(r);else next.put(editingIndex,r);if(!persist(next)){toast("保存失败，草稿已保留");return;}records=next;for(int i=0;i<originalPhotos.length();i++){JSONObject p=originalPhotos.optJSONObject(i);if(p!=null&&!containsFile(draft,p.optString("file")))photoFile(p.optString("file")).delete();}newDraft();go("records");}catch(JSONException e){toast("保存失败，草稿已保留");}}
    private void discard(){if(!storageReady){toast("保存状态不确定，请关闭应用后重新打开");return;}new AlertDialog.Builder(this).setTitle("放弃当前修改？").setMessage("尚未保存的修改将被放弃。") .setNegativeButton("继续编辑",null).setPositiveButton("放弃",(d,w)->{for(int i=0;i<draft.length();i++){JSONObject p=draft.optJSONObject(i);if(p!=null&&!containsFile(originalPhotos,p.optString("file")))photoFile(p.optString("file")).delete();}newDraft();go("home");}).show();}
    private void settings(){section("设置", "本机数据与应用信息");LinearLayout c=card();add(c,text("本机数据",16,INK,true));gap(c,7);add(c,text("记录与图片保存在应用私有目录，卸载应用或清除应用数据会移除它们。",13,MUTED,false));gap(c,16);add(c,button("导出记录与照片",false,this::chooseBackup));gap(c,16);add(c,text("网页端互联",16,INK,true));gap(c,7);add(c,text("手动配置服务地址；通过系统浏览器打开网页端，账户和资料由网页服务处理。本机记录不会自动同步或上传。",13,MUTED,false));gap(c,16);add(c,button("设置网页服务地址",false,this::configureWebsite));gap(c,8);add(c,button("在浏览器中打开网页端",false,this::openWebsite));gap(c,16);add(c,text("隐私说明",16,INK,true));gap(c,7);add(c,text("开发者：刘思辰。你主动填写的问卷、输入的文字、拍摄和选择的图片保存在本机，用于展示和管理记录。你可在应用内删除记录；卸载或清除数据会删除应用内保存的内容。使用系统相机和图片选择器时，相关系统应用的处理行为以其说明为准。主动打开网页服务时，网页中的账号和数据由对应网站及服务器处理，本机照片不会自动上传。",13,MUTED,false));gap(c,16);add(c,text("联系我们",16,INK,true));gap(c,7);add(c,text("benjaminmcree@qq.com",13,MUTED,false));add(content,c);}
    private boolean isPrivateIpv4(String host){
        if(host==null||!host.matches("[0-9]{1,3}(\\.[0-9]{1,3}){3}"))return false;
        String[] parts=host.split("\\.");int[] ip=new int[4];
        try{for(int i=0;i<4;i++){if(parts[i].length()>1&&parts[i].startsWith("0"))return false;ip[i]=Integer.parseInt(parts[i]);if(ip[i]>255)return false;}}
        catch(NumberFormatException e){return false;}
        return ip[0]==10||(ip[0]==172&&ip[1]>=16&&ip[1]<=31)||(ip[0]==192&&ip[1]==168);
    }
    private String validWebsite(String candidate){
        if(candidate==null||candidate.length()>2048)return null;
        String value=candidate.trim();
        try {
            java.net.URI url=new java.net.URI(value);
            String scheme=url.getScheme(), host=url.getHost(), path=url.getRawPath();
            if(host==null||host.equalsIgnoreCase("localhost")||host.startsWith("127.")||(host.equals("::1")||host.equals("[::1]"))||url.getRawUserInfo()!=null||url.getRawQuery()!=null||url.getRawFragment()!=null||url.getPort()>65535||url.getPort()==0||(path!=null&&!path.isEmpty()&&!path.equals("/")))return null;
            if(!"https".equalsIgnoreCase(scheme)&&!("http".equalsIgnoreCase(scheme)&&isPrivateIpv4(host)))return null;
            if(value.toLowerCase(Locale.ROOT).contains("%0d")||value.toLowerCase(Locale.ROOT).contains("%0a"))return null;
            return url.toASCIIString();
        } catch(java.net.URISyntaxException e){return null;}
    }
    private void configureWebsite(){
        LinearLayout form=vertical();form.setPadding(dp(24),dp(4),dp(24),0);
        add(form,text("输入网站根地址。网页将在系统浏览器中打开；本机记录和图片不会自动上传。电脑服务需配置为手机可访问；手机和电脑通常需要在同一局域网，默认仅本机监听的服务无法从手机访问。",13,MUTED,false));
        EditText address=new EditText(this);address.setSingleLine(true);address.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_URI);
        address.setHint("https://网址 或 http://192.168.1.10:8000");address.setText(getPreferences(MODE_PRIVATE).getString("websiteUrl",""));add(form,address);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("网页服务地址").setView(form).setNegativeButton("取消",null)
            .setNeutralButton("清除",(d,w)->{if(getPreferences(MODE_PRIVATE).edit().remove("websiteUrl").commit())toast("地址已清除");else toast("保存失败");})
            .setPositiveButton("保存",null).create();
        dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{String url=validWebsite(address.getText().toString());
            if(url==null){address.setError("请输入 HTTPS 根地址，或 http://局域网IPv4:端口；不要包含账号、密码、路径或查询参数");return;}
            if(url.toLowerCase(Locale.ROOT).startsWith("http://")){
                new AlertDialog.Builder(this).setTitle("确认使用局域网 HTTP？").setMessage("手机和电脑需要连接同一局域网，且电脑服务须允许手机访问。HTTP 传输未加密，同一网络中的其他人可能读取或篡改内容；不适合传输敏感信息。建议在可信网络中测试，正式使用请配置 HTTPS。")
                    .setNegativeButton("取消",null).setPositiveButton("确认保存",(confirm,w)->storeWebsite(url,dialog)).show();
            }else storeWebsite(url,dialog);}));
        dialog.show();
    }
    private void storeWebsite(String url,AlertDialog dialog){if(getPreferences(MODE_PRIVATE).edit().putString("websiteUrl",url).commit()){dialog.dismiss();toast("地址已保存");}else toast("保存失败");}
    private void openWebsite(){
        String url=validWebsite(getPreferences(MODE_PRIVATE).getString("websiteUrl",""));
        if(url==null){configureWebsite();return;}
        try{startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE));}
        catch(android.content.ActivityNotFoundException e){toast("未找到可打开网页的浏览器");}
    }
    private boolean validPhotoName(String name){return name!=null&&name.matches("[a-f0-9-]{36}\\.(jpg|png|webp|gif|bmp|heif|heic|avif|img)");}
    private void chooseImageExport(String name){
        if(exporting){toast("正在导出，请稍候");return;}
        if(!validPhotoName(name)){toast("图片路径无效");return;}
        File image=photoFile(name);if(!image.isFile()){toast("未找到图片文件");return;}
        BitmapFactory.Options probe=new BitmapFactory.Options();probe.inJustDecodeBounds=true;BitmapFactory.decodeFile(image.getAbsolutePath(),probe);
        if(probe.outWidth<=0||probe.outHeight<=0){toast("无法读取图片文件");return;}
        String type=probe.outMimeType;if(type==null||!type.matches("image/[A-Za-z0-9.+-]+"))type="image/*";
        Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT);intent.addCategory(Intent.CATEGORY_OPENABLE);intent.setType(type);
        intent.putExtra(Intent.EXTRA_TITLE,"SpineInsight-"+name.substring(0,8)+name.substring(name.lastIndexOf('.')));
        new AlertDialog.Builder(this).setTitle("导出原图")
            .setMessage("图片将保存到你选择的位置，之后可在浏览器中手动选择。原图可能包含拍摄时间、设备或位置信息，请确认后分享。")
            .setNegativeButton("取消",null).setPositiveButton("选择保存位置",(d,w)->{
                pendingExportImage=name;try{startActivityForResult(intent,EXPORT_IMAGE);}catch(Exception e){pendingExportImage="";toast("无法打开系统文件保存界面");}
            }).show();
    }
    private void exportImage(String name,Uri destination){
        if(exporting){toast("正在导出，请稍候");return;}
        if(!validPhotoName(name)||!photoFile(name).isFile()){toast("未找到图片文件");return;}
        exporting=true;toast("正在导出，请稍候");
        new Thread(()->{try{
            try(java.io.FileInputStream in=new java.io.FileInputStream(photoFile(name));java.io.OutputStream out=getContentResolver().openOutputStream(destination,"w")){
                if(out==null)throw new java.io.IOException("destination unavailable");
                byte[] buffer=new byte[16384];int n;while((n=in.read(buffer))!=-1)out.write(buffer,0,n);
            }
            runOnUiThread(()->toast("图片已导出，可在浏览器中选择该文件"));
        }catch(Exception e){runOnUiThread(()->toast("图片导出失败，请重新选择保存位置"));}finally{exporting=false;}}).start();
    }
    private void chooseBackup(){new AlertDialog.Builder(this).setTitle("导出本机备份")
        .setMessage("备份包含档案中的文字和照片，不包含问卷；请选择你信任的位置保存。当前版本暂不支持导入恢复。")
        .setNegativeButton("取消",null).setPositiveButton("选择保存位置",(d,w)->{
            Intent i=new Intent(Intent.ACTION_CREATE_DOCUMENT);i.addCategory(Intent.CATEGORY_OPENABLE);i.setType("application/zip");
            i.putExtra(Intent.EXTRA_TITLE,"SpineInsight-"+new SimpleDateFormat("yyyyMMdd-HHmm",Locale.CHINA).format(new Date())+".zip");
            try{startActivityForResult(i,EXPORT);}catch(Exception e){toast("无法打开系统文件保存界面");}
        }).show();}
    private void exportBackup(Uri destination){
        if(exporting){toast("正在导出，请稍候");return;} exporting=true; final String snapshot=records.toString();toast("正在导出，请稍候");
        new Thread(()->{try{
            JSONArray saved=new JSONArray(snapshot);JSONObject manifest=new JSONObject();manifest.put("format","spineinsight.android.local.v1");manifest.put("records",saved);
            java.io.OutputStream stream=getContentResolver().openOutputStream(destination,"wt"); if(stream==null)throw new java.io.IOException("destination unavailable");
            try(java.util.zip.ZipOutputStream zip=new java.util.zip.ZipOutputStream(stream)){
                zip.putNextEntry(new java.util.zip.ZipEntry("records.json"));zip.write(manifest.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));zip.closeEntry();
                java.util.HashSet<String> included=new java.util.HashSet<>(); byte[] buf=new byte[16384];
                for(int i=0;i<saved.length();i++){JSONObject record=saved.optJSONObject(i);JSONArray photos=record==null?null:record.optJSONArray("photos");if(photos==null)continue;
                    for(int j=0;j<photos.length();j++){JSONObject photo=photos.optJSONObject(j);if(photo==null)continue;String name=photo.optString("file");
                        if(!name.matches("[a-f0-9-]{36}\\.(jpg|png|webp|gif|bmp|heif|heic|avif|img)"))throw new java.io.IOException("invalid photo name");if(!included.add(name))continue;
                        File image=photoFile(name);if(!image.isFile())throw new java.io.IOException("missing photo");
                        zip.putNextEntry(new java.util.zip.ZipEntry("photos/"+name));try(java.io.FileInputStream in=new java.io.FileInputStream(image)){int n;while((n=in.read(buf))!=-1)zip.write(buf,0,n);}zip.closeEntry();
                    }
                }
            }
            runOnUiThread(()->toast("备份已导出"));
        }catch(Exception e){runOnUiThread(()->toast("导出失败，请重新选择保存位置"));}finally{exporting=false;}}).start();
    }
    private void toast(String s){Toast.makeText(this,s,Toast.LENGTH_LONG).show();}
    @Override public void onBackPressed(){if(page.equals("guide")){go("editor");}else if(page.equals("editor")){discard();}else if(page.equals("detail")){go("records");}else if(!page.equals("home")){go("home");}else super.onBackPressed();}
}
