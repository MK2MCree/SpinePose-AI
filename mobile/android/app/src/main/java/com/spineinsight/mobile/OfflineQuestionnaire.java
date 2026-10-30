package com.spineinsight.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.text.InputFilter;
import android.text.Editable;
import android.text.TextWatcher;
import android.util.Base64;
import android.view.View;
import android.view.Window;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/** Local transcription of the server's spine-intake-v3 schema. No triage is performed. */
public final class OfflineQuestionnaire {
    // Generated from mindspine.questionnaire.questionnaire_schema(), never from user input.
    private static final String SCHEMA = "eyJ2ZXJzaW9uIjoic3BpbmUtaW50YWtlLXYzIiwidGl0bGUiOiLohIrmn7HlgaXlurfkuI7ml6XluLjlip/og73pl67ljbciLCJyZXZpZXdfc3RhdHVzIjoibm90X2NsaW5pY2FsbHlfdmFsaWRhdGVkIiwibm90aWNlIjoi6aG555uu6Ieq57yW5oiQ5Lq65L+h5oGv6YeH6ZuG6KGo77yM5Y+C6ICD5oyH5Y2X55qE6K+E5Lyw6aKG5Z+f77yb5pyq57uP6L+H5Li05bqK6YeP6KGo6aqM6K+B77yM5LiN6IO96K+K5pat44CB5o6S6Zmk55a+55eF5oiW5pu/5Luj5Yy755Sf44CCIiwic291cmNlcyI6W3sidGl0bGUiOiJOSUNFIE5HNTnvvJrohbDog4znl5vkuI7lnZDpqqjnpZ7nu4/nl5vnmoTor4TkvLDlkoznrqHnkIYiLCJ1cmwiOiJodHRwczovL3d3dy5uaWNlLm9yZy51ay9ndWlkYW5jZS9uZzU5L2NoYXB0ZXIvUmVjb21tZW5kYXRpb25zIn0seyJ0aXRsZSI6Ik5IU++8muiDjOeXm+S4jumcgOimgeWPiuaXtuWwseWMu+eahOeXh+eKtiIsInVybCI6Imh0dHBzOi8vd3d3Lm5ocy51ay9jb25kaXRpb25zL2JhY2stcGFpbi8ifV0sInNlY3Rpb25zIjpbeyJpZCI6InN0YXJ0IiwidGl0bGUiOiLlhYjnoa7orqTmnKzmrKHloavlhpkiLCJkZXNjcmlwdGlvbiI6Iue6pumcgCA14oCTOCDliIbpkp/jgILmjInnjrDlnKjnmoTlrp7pmYXmg4XlhrXlm57nrZTvvJvkuI3noa7lrprlj6/ku6XlpoLlrp7pgInmi6nvvIzkuI3kvJrooqvlvZPkvZzigJzmsqHmnInpl67popjigJ3jgIIiLCJmaWVsZHMiOlt7ImlkIjoiYWR1bHRfY29uZmlybWVkIiwibGFiZWwiOiLkvaDmmK/lkKblt7Lmu6EgMTgg5ZGo5bKB77yfIiwidHlwZSI6ImNob2ljZSIsInJlcXVpcmVkIjp0cnVlLCJoZWxwIjoiIiwib3B0aW9ucyI6W3sidmFsdWUiOiJ5ZXMiLCJsYWJlbCI6IuW3sua7oSAxOCDlkajlsoEifSx7InZhbHVlIjoibm8iLCJsYWJlbCI6Iuacqua7oSAxOCDlkajlsoEifV19LHsiaWQiOiJjb25zZW50X2NvbmZpcm1lZCIsInR5cGUiOiJjb25zZW50IiwicmVxdWlyZWQiOnRydWUsImxhYmVsIjoi5oiR5ZCM5oSP5bCG5pys5qyh5Zue562U5L+d5a2Y5Yiw5b2T5YmN5YGl5bq35qGj5qGI77yM55So5LqO6LWE5paZ5pW055CG5LiO5a6J5YWo5YiG5rWB44CCIiwiaGVscCI6IuacrOmXruWNt+S4jeaYr+iviuaWreOAgue0p+aApeS4jemAguaXtuS4jeimgeetieW+hemXruWNt+aIliBBSSDlm57lpI3vvJvljp/lp4votYTmlpnkuI3kvJrlm6DmraTlj5HpgIHliLDlpJbpg6jmqKHlnovjgIIifV19LHsiaWQiOiJzYWZldHkiLCJ0aXRsZSI6IuWFiOaOkuafpemcgOimgeWPiuaXtuWwseWMu+eahOaDheWGtSIsImRlc2NyaXB0aW9uIjoi5q+P5LiA6aG56YO96K+35piO56Gu6YCJ5oup44CC5Ye6546w5paw5Y+R5aSn5bCP5L6/5o6n5Yi25byC5bi444CB5Lya6Zi06bq75pyo44CB6IW/6YOo5peg5Yqb5oiW5Lil6YeN5aSW5Lyk5pe277yM5bqU56uL5Y2z5a+75rGC5oCl6K+K6K+E5Lyw77yM5LiN5b+F562J5Yiw5aGr5YaZ57uT5p2f44CCIiwiZmllbGRzIjpbeyJpZCI6InJlZF9mbGFnX2Fuc3dlcnMubmV3X2Jvd2VsX29yX2JsYWRkZXJfY2hhbmdlIiwibGFiZWwiOiLmlrDlh7rnjrDmjpLlsL/jgIHmjpLkvr/mjqfliLblj5jljJYiLCJ0eXBlIjoiY2hvaWNlIiwicmVxdWlyZWQiOnRydWUsImhlbHAiOiLkvovlpoLnqoHnhLblsL/kuI3lh7rmnaXjgIHmhJ/op4nkuI3liLDohoDog7HmmK/lkKblhYXnm4jvvIzmiJbku6XliY3msqHmnInnmoTmvI/lsL/jgIHlpLHnpoHvvJvkuI3mmK/ljZXnuq/kvr/np5jjgIIiLCJvcHRpb25zIjpbeyJ2YWx1ZSI6InllcyIsImxhYmVsIjoi5pyJIn0seyJ2YWx1ZSI6Im5vIiwibGFiZWwiOiLmsqHmnIkifSx7InZhbHVlIjoidW5rbm93biIsImxhYmVsIjoi5LiN56Gu5a6aIn1dLCJzZXZlcml0eSI6M30seyJpZCI6InJlZF9mbGFnX2Fuc3dlcnMuc2FkZGxlX251bWJuZXNzIiwibGFiZWwiOiLoh4Dpg6jlhoXkvqfjgIHkvJrpmLTmiJbogpvpl6jlkajlm7TmlrDlh7rnjrDpurvmnKgiLCJ0eXBlIjoiY2hvaWNlIiwicmVxdWlyZWQiOnRydWUsImhlbHAiOiLmjIflnZDlnKjoh6rooYzovablnZDlnqvkuIrmjqXop6bnmoTljLrln5/vvIzkvovlpoLmk6bmi63ml7bmhJ/op4nmmI7mmL7lj5jlvLHjgIIiLCJvcHRpb25zIjpbeyJ2YWx1ZSI6InllcyIsImxhYmVsIjoi5pyJIn0seyJ2YWx1ZSI6Im5vIiwibGFiZWwiOiLmsqHmnIkifSx7InZhbHVlIjoidW5rbm93biIsImxhYmVsIjoi5LiN56Gu5a6aIn1dLCJzZXZlcml0eSI6M30seyJpZCI6InJlZF9mbGFnX2Fuc3dlcnMucHJvZ3Jlc3NpdmVfd2Vha25lc3MiLCJsYWJlbCI6IuiFv+mDqOWKm+mHj+ato+WcqOaYjuaYvuWPmOW3riIsInR5cGUiOiJjaG9pY2UiLCJyZXF1aXJlZCI6dHJ1ZSwiaGVscCI6IuS+i+Wmgui/keacn+i2iuadpei2iumavuaKrOiEmuOAgeiEmuaLluWcsOaIluermeeri+S4jeeos++8m+S4jeaYr+i/kOWKqOWQjueahOaZrumAmueWsuWKs+OAgiIsIm9wdGlvbnMiOlt7InZhbHVlIjoieWVzIiwibGFiZWwiOiLmnIkifSx7InZhbHVlIjoibm8iLCJsYWJlbCI6IuayoeaciSJ9LHsidmFsdWUiOiJ1bmtub3duIiwibGFiZWwiOiLkuI3noa7lrpoifV0sInNldmVyaXR5IjozfSx7ImlkIjoicmVkX2ZsYWdfYW5zd2Vycy5iaWxhdGVyYWxfbGVnX3N5bXB0b21zIiwibGFiZWwiOiLog4znl5vlkIzml7bkvLTlj4zohb/mlrDlh7rnjrDnlrznl5vjgIHpurvmnKjmiJbml6DlipsiLCJ0eXBlIjoiY2hvaWNlIiwicmVxdWlyZWQiOnRydWUsImhlbHAiOiLov5nph4zmjIfkuKTmnaHohb/lkIzml7blh7rnjrDnmoTmlrDlj5jljJbvvIzogIzkuI3lj6rmmK/ljZXkvqfplb/mnJ/phbjnl5vjgIIiLCJvcHRpb25zIjpbeyJ2YWx1ZSI6InllcyIsImxhYmVsIjoi5pyJIn0seyJ2YWx1ZSI6Im5vIiwibGFiZWwiOiLmsqHmnIkifSx7InZhbHVlIjoidW5rbm93biIsImxhYmVsIjoi5LiN56Gu5a6aIn1dLCJzZXZlcml0eSI6M30seyJpZCI6InJlZF9mbGFnX2Fuc3dlcnMuc2V2ZXJlX3RyYXVtYSIsImxhYmVsIjoi5pys5qyh5LiN6YCC5Y+R55Sf5Zyo5Lil6YeN5LqL5pWF5oiW5piO5pi+5aSW5Lyk5LmL5ZCOIiwidHlwZSI6ImNob2ljZSIsInJlcXVpcmVkIjp0cnVlLCJoZWxwIjoi5L6L5aaC6L2m56W444CB6auY5aSE5Z2g6JC95oiW6YeN54mp5pKe5Ye744CC6L275b6u56Kw5pKe5LiU5LiN56Gu5a6a5pe26K+36YCJ5oup4oCc5LiN56Gu5a6a4oCd44CCIiwib3B0aW9ucyI6W3sidmFsdWUiOiJ5ZXMiLCJsYWJlbCI6IuaciSJ9LHsidmFsdWUiOiJubyIsImxhYmVsIjoi5rKh5pyJIn0seyJ2YWx1ZSI6InVua25vd24iLCJsYWJlbCI6IuS4jeehruWumiJ9XSwic2V2ZXJpdHkiOjN9LHsiaWQiOiJyZWRfZmxhZ19hbnN3ZXJzLmZldmVyX29yX2luZmVjdGlvbiIsImxhYmVsIjoi6IOM55eb5ZCM5pe25Ly05Y+R54Ot44CB5a+S5oiY5oiW5piO5pi+5YWo6Lqr5LiN6YCCIiwidHlwZSI6ImNob2ljZSIsInJlcXVpcmVkIjp0cnVlLCJoZWxwIjoi5bCk5YW25piv6L+R5pyf5oSf5p+T5LmL5ZCO5Ye6546w77yb5LiN6KaB5Y+q5oqK5a6D5b2T5L2c6IKM6IKJ6YW455eb44CCIiwib3B0aW9ucyI6W3sidmFsdWUiOiJ5ZXMiLCJsYWJlbCI6IuaciSJ9LHsidmFsdWUiOiJubyIsImxhYmVsIjoi5rKh5pyJIn0seyJ2YWx1ZSI6InVua25vd24iLCJsYWJlbCI6IuS4jeehruWumiJ9XSwic2V2ZXJpdHkiOjJ9LHsiaWQiOiJyZWRfZmxhZ19hbnN3ZXJzLmNhbmNlcl9oaXN0b3J5X3dpdGhfbmV3X3BhaW4iLCJsYWJlbCI6IuabvuaCo+iCv+eYpO+8jOiAjOS4lOi/measoeWHuueOsOaWsOeahOaIluWKoOmHjeeahOiDjOeXmyIsInR5cGUiOiJjaG9pY2UiLCJyZXF1aXJlZCI6dHJ1ZSwiaGVscCI6IuiLpeS4jeS6huino+aXouW+gOiviuaWre+8jOmAieaLqeKAnOS4jeehruWumuKAne+8jOS4jeimgeeMnOa1i+OAgiIsIm9wdGlvbnMiOlt7InZhbHVlIjoieWVzIiwibGFiZWwiOiLmnIkifSx7InZhbHVlIjoibm8iLCJsYWJlbCI6IuayoeaciSJ9LHsidmFsdWUiOiJ1bmtub3duIiwibGFiZWwiOiLkuI3noa7lrpoifV0sInNldmVyaXR5IjoyfSx7ImlkIjoicmVkX2ZsYWdfYW5zd2Vycy51bmV4cGxhaW5lZF93ZWlnaHRfbG9zcyIsImxhYmVsIjoi6L+R5pyf5rKh5pyJ5Li75Yqo5YeP6YeN77yM5Y205piO5pi+5Y+Y55imIiwidHlwZSI6ImNob2ljZSIsInJlcXVpcmVkIjp0cnVlLCJoZWxwIjoi5L6L5aaC5pyq5pS55Y+Y6aWu6aOf5oiW6L+Q5Yqo77yM5L2T6YeN5Y205oyB57ut5LiL6ZmN77yb5Y+v5Zyo6KGl5YWF6K+05piO5Lit5YaZ5Y+Y5YyW44CCIiwib3B0aW9ucyI6W3sidmFsdWUiOiJ5ZXMiLCJsYWJlbCI6IuaciSJ9LHsidmFsdWUiOiJubyIsImxhYmVsIjoi5rKh5pyJIn0seyJ2YWx1ZSI6InVua25vd24iLCJsYWJlbCI6IuS4jeehruWumiJ9XSwic2V2ZXJpdHkiOjJ9LHsiaWQiOiJyZWRfZmxhZ19hbnN3ZXJzLm5pZ2h0X3BhaW4iLCJsYWJlbCI6IueWvOeXm+e7j+W4uOWcqOWknOmXtOWKoOmHjeaIluS8keaBr+WQjuS7jeS4jee8k+inoyIsInR5cGUiOiJjaG9pY2UiLCJyZXF1aXJlZCI6dHJ1ZSwiaGVscCI6IuS+i+WmguWkmuasoeiiq+eWvOmGkuOAgeaNouWnv+WKv+S5n+ayoeacieaYjuaYvue8k+ino+OAguWNleasoeedoeWnv+S4jemAguS4jeetieS6juatpOaDheWGteOAgiIsIm9wdGlvbnMiOlt7InZhbHVlIjoieWVzIiwibGFiZWwiOiLmnIkifSx7InZhbHVlIjoibm8iLCJsYWJlbCI6IuayoeaciSJ9LHsidmFsdWUiOiJ1bmtub3duIiwibGFiZWwiOiLkuI3noa7lrpoifV0sInNldmVyaXR5IjoxfV19LHsiaWQiOiJzeW1wdG9tcyIsInRpdGxlIjoi5o+P6L+w5LiN6YCC77yM6ICM5LiN5piv54yc6K+K5patIiwiZGVzY3JpcHRpb24iOiLnlrznl5vliIbmlbDlj6rorrDlvZXkvaDnmoTkuLvop4LmhJ/lj5fvvIzkuI3og73liKTmlq3nlr7nl4Xnp43nsbvmiJbkuKXph43nqIvluqbjgIIiLCJmaWVsZHMiOlt7ImlkIjoic3ltcHRvbV9sb2NhdGlvbiIsInR5cGUiOiJtdWx0aSIsImxhYmVsIjoi5Li76KaB5LiN6YCC5Zyo5ZOq6YeM77yfIiwicmVxdWlyZWQiOnRydWUsImV4Y2x1c2l2ZSI6IuW9k+WJjeaXoOaYjuaYvuS4jemAgiIsIm9wdGlvbnMiOlt7InZhbHVlIjoi6aKI6IKp6YOoIiwibGFiZWwiOiLpoojogqnpg6gifSx7InZhbHVlIjoi5LiK6IOM6YOoIiwibGFiZWwiOiLkuIrog4zpg6gifSx7InZhbHVlIjoi6IWw6YOoIiwibGFiZWwiOiLohbDpg6gifSx7InZhbHVlIjoi6aq26IeA6YOoIiwibGFiZWwiOiLpqrboh4Dpg6gifSx7InZhbHVlIjoi5LiL6IKiIiwibGFiZWwiOiLkuIvogqIifSx7InZhbHVlIjoi5b2T5YmN5peg5piO5pi+5LiN6YCCIiwibGFiZWwiOiLlvZPliY3ml6DmmI7mmL7kuI3pgIIifV19LHsiaWQiOiJkdXJhdGlvbiIsImxhYmVsIjoi5pys5qyh5LiN6YCC5oyB57ut5LqG5aSa5LmF77yfIiwidHlwZSI6ImNob2ljZSIsInJlcXVpcmVkIjp0cnVlLCJoZWxwIjoiIiwib3B0aW9ucyI6W3sidmFsdWUiOiLkuI3otrMgMSDlkagiLCJsYWJlbCI6IuS4jei2syAxIOWRqCJ9LHsidmFsdWUiOiIx4oCTNCDlkagiLCJsYWJlbCI6IjHigJM0IOWRqCJ9LHsidmFsdWUiOiIx4oCTMyDkuKrmnIgiLCJsYWJlbCI6IjHigJMzIOS4quaciCJ9LHsidmFsdWUiOiLotoXov4cgMyDkuKrmnIgiLCJsYWJlbCI6Iui2hei/hyAzIOS4quaciCJ9LHsidmFsdWUiOiLlj43lpI3lj5HkvZwiLCJsYWJlbCI6IuWPjeWkjeWPkeS9nCJ9LHsidmFsdWUiOiLlvZPliY3ml6Dnl4fnirbvvIzov5vooYzmo4Dmn6XmiJbpmo/orr8iLCJsYWJlbCI6IuW9k+WJjeaXoOeXh+eKtu+8jOi/m+ihjOajgOafpeaIlumaj+iuvyJ9XX0seyJpZCI6InN5bXB0b21fY2hhbmdlIiwibGFiZWwiOiLkuI7kuIDlkajliY3nm7jmr5TvvIzmlbTkvZPmhJ/op4nmgI7moLfvvJ8iLCJ0eXBlIjoiY2hvaWNlIiwicmVxdWlyZWQiOnRydWUsImhlbHAiOiIiLCJvcHRpb25zIjpbeyJ2YWx1ZSI6IuacieaJgOWHj+i9uyIsImxhYmVsIjoi5pyJ5omA5YeP6L27In0seyJ2YWx1ZSI6IuWfuuacrOS4jeWPmCIsImxhYmVsIjoi5Z+65pys5LiN5Y+YIn0seyJ2YWx1ZSI6IumAkOa4kOWKoOmHjSIsImxhYmVsIjoi6YCQ5riQ5Yqg6YeNIn0seyJ2YWx1ZSI6IueqgeeEtuaYjuaYvuWKoOmHjSIsImxhYmVsIjoi56qB54S25piO5pi+5Yqg6YeNIn0seyJ2YWx1ZSI6IueXh+eKtuS4jei2s+S4gOWRqCIsImxhYmVsIjoi55eH54q25LiN6Laz5LiA5ZGoIn0seyJ2YWx1ZSI6IuW9k+WJjeaXoOeXh+eKtiIsImxhYmVsIjoi5b2T5YmN5peg55eH54q2In1dfSx7ImlkIjoicmFkaWF0aW9uIiwibGFiZWwiOiLkuI3pgILmmK/lkKbku47og4zpg6jmiJboh4Dpg6jlu7bkvLjliLDohb/pg6jvvJ8iLCJ0eXBlIjoiY2hvaWNlIiwicmVxdWlyZWQiOnRydWUsImhlbHAiOiLov5nlj6vigJzmlL7lsITigJ3vvIzkvovlpoLnlrznl5vmsr/oh4Dpg6jliLDlpKfohb/miJblsI/ohb/vvJvlroPmnKzouqvkuI3nrYnkuo7noa7or4rnpZ7nu4/lj5fljovjgIIiLCJvcHRpb25zIjpbeyJ2YWx1ZSI6IuayoeaciSIsImxhYmVsIjoi5rKh5pyJIn0seyJ2YWx1ZSI6IuS4u+imgeWIsOW3puiFvyIsImxhYmVsIjoi5Li76KaB5Yiw5bem6IW/In0seyJ2YWx1ZSI6IuS4u+imgeWIsOWPs+iFvyIsImxhYmVsIjoi5Li76KaB5Yiw5Y+z6IW/In0seyJ2YWx1ZSI6IuS4pOadoeiFv+mDveaciSIsImxhYmVsIjoi5Lik5p2h6IW/6YO95pyJIn0seyJ2YWx1ZSI6IuS4jeehruWumiIsImxhYmVsIjoi5LiN56Gu5a6aIn1dfSx7ImlkIjoicGFpbl9zY29yZSIsInR5cGUiOiJzY29yZSIsInJlcXVpcmVkIjp0cnVlLCJsYWJlbCI6IuatpOWIu+eahOeWvOeXm+Wkp+e6puaYr+WkmuWwke+8nyIsImhlbHAiOiLlhYjnnIvkuIvpnaLnmoTnlJ/mtLvkvovlrZDvvIzlho3pgInmi6nmnIDmjqXov5HnmoQgMOKAkzEwIOWIhuOAguS+i+WtkOWPquaYr+W4ruWKqeihqOi+vu+8jOS4jeaYr+e7j+i/h+mqjOivgeeahOiviuaWreWIhue6p+OAgiIsIm1pbiI6MCwibWF4IjoxMCwiYW5jaG9ycyI6W3sibGFiZWwiOiIwIMK3IOaXoOeXmyIsImRldGFpbCI6IuatpOWIu+WujOWFqOS4jeeXm+OAgiJ9LHsibGFiZWwiOiIx4oCTMyDCtyDog73mhJ/op4nliLAiLCJkZXRhaWwiOiLmnInphbjnl5vmiJbpmpDnl5vvvIzlpKflpJrmlbDml6XluLjkuovmg4Xov5jog73nhafluLjlgZrjgIIifSx7ImxhYmVsIjoiNOKAkzYgwrcg5piO5pi+5b2x5ZONIiwiZGV0YWlsIjoi5bi46KKr55a855eb5YiG5pWj5rOo5oSP5Yqb77yM5bel5L2c44CB6LWw6Lev5oiW552h55yg6ZyA6KaB6LCD5pW044CCIn0seyJsYWJlbCI6IjfigJM5IMK3IOS4pemHjeW5suaJsCIsImRldGFpbCI6IuW+iOWkmuS6i+aDheWBmuS4jeS6hu+8jOS8keaBr+S5n+mavuWPl++8jOmcgOimgeWPiuaXtuS4k+S4muivhOS8sOOAgiJ9LHsibGFiZWwiOiIxMCDCtyDpmr7ku6Xlv43lj5ciLCJkZXRhaWwiOiLkvaDog73mg7PosaHnmoTmnIDlvLrnlrznl5vvvJvnqoHnhLblj5HnlJ/miJbkvLTlvILluLjnl4fnirbml7bkuI3opoHnrYnlvoXnur/kuIrpl67ljbfjgIIifV19LHsiaWQiOiJzeW1wdG9tX25vdGVzIiwidHlwZSI6InRleHQiLCJsYWJlbCI6IuS7gOS5iOaDheWGteabtOeXm+aIluabtOiIkuacje+8n++8iOmAieWhq++8iSIsInJlcXVpcmVkIjpmYWxzZSwiaGVscCI6IuS+i+WmguS5heWdkOOAgeW8r+iFsOOAgei1sOWKqOWQjuWPmOWMlu+8m+WPr+S7peihpeWFheWFt+S9k+mDqOS9jeaIluWPkeeUn+aXtumXtOOAgiIsIm1heF9sZW5ndGgiOjEyMDB9XX0seyJpZCI6ImZ1bmN0aW9uIiwidGl0bGUiOiLnnIvnnIvlr7nml6XluLjnlJ/mtLvnmoTlvbHlk40iLCJkZXNjcmlwdGlvbiI6Iuivt+WbnuaDs+acgOi/kSA3IOWkqe+8m+WPquiusOW9leS4juacrOasoemiiOiDjOiFsOmDqOmXrumimOacieWFs+eahOW9seWTjeOAgui/meWbm+mhueWIhuWIq+iusOW9le+8jOS4jeWQiOaIkOS4uuKAnOiEiuafseWBpeW6t+aAu+WIhuKAneOAgiIsImZpZWxkcyI6W3siaWQiOiJkYWlseV9mdW5jdGlvbi53YWxraW5nIiwibGFiZWwiOiLmraXooYzmiJblpJblh7rml7YiLCJ0eXBlIjoiY2hvaWNlIiwicmVxdWlyZWQiOnRydWUsImhlbHAiOiIiLCJvcHRpb25zIjpbeyJ2YWx1ZSI6Im5vbmUiLCJsYWJlbCI6IuayoeacieW9seWTje+8jOWSjOW5s+aXtuS4gOagtyJ9LHsidmFsdWUiOiJtaWxkIiwibGFiZWwiOiLog73otbDvvIzkvYbkvJrkuI3oiJLmnI0ifSx7InZhbHVlIjoibW9kZXJhdGUiLCJsYWJlbCI6IuimgeWHj+Wwkei3neemu+aIluS4remAlOWBnOS4i+adpeS8keaBryJ9LHsidmFsdWUiOiJzZXZlcmUiLCJsYWJlbCI6IuW+iOmavuihjOi1sO+8jOaIlumcgOimgeWIq+S6uuW4ruWKqSJ9LHsidmFsdWUiOiJ1bmtub3duIiwibGFiZWwiOiLkuI3noa7lrpogLyDov5HmnJ/msqHmnInlgZrov4cifV19LHsiaWQiOiJkYWlseV9mdW5jdGlvbi5zaXR0aW5nIiwibGFiZWwiOiLlnZDnnYDlt6XkvZzmiJblrabkuaDml7YiLCJ0eXBlIjoiY2hvaWNlIiwicmVxdWlyZWQiOnRydWUsImhlbHAiOiIiLCJvcHRpb25zIjpbeyJ2YWx1ZSI6Im5vbmUiLCJsYWJlbCI6IuayoeacieW9seWTje+8jOWSjOW5s+aXtuS4gOagtyJ9LHsidmFsdWUiOiJtaWxkIiwibGFiZWwiOiLog73lrozmiJDvvIzpnIDopoHlgbblsJTmjaLlp7/lir8ifSx7InZhbHVlIjoibW9kZXJhdGUiLCJsYWJlbCI6IumcgOimgemikee5gei1t+i6q+aIluWHj+WwkeW3peS9nOaXtumXtCJ9LHsidmFsdWUiOiJzZXZlcmUiLCJsYWJlbCI6IumavuS7pee7p+e7reWdkOedgOWujOaIkOaXpeW4uOS6i+aDhSJ9LHsidmFsdWUiOiJ1bmtub3duIiwibGFiZWwiOiLkuI3noa7lrpogLyDov5HmnJ/msqHmnInlgZrov4cifV19LHsiaWQiOiJkYWlseV9mdW5jdGlvbi5zbGVlcCIsImxhYmVsIjoi5aSc6Ze0552h6KeJ5pe2IiwidHlwZSI6ImNob2ljZSIsInJlcXVpcmVkIjp0cnVlLCJoZWxwIjoiIiwib3B0aW9ucyI6W3sidmFsdWUiOiJub25lIiwibGFiZWwiOiLmsqHmnInlvbHlk43vvIzlkozlubPml7bkuIDmoLcifSx7InZhbHVlIjoibWlsZCIsImxhYmVsIjoi5YWl552h56iN5Zuw6Zq+5oiW6ZyA6KaB5o2i5ae/5Yq/In0seyJ2YWx1ZSI6Im1vZGVyYXRlIiwibGFiZWwiOiLkvJrooqvnlrzphpLvvIznrKzkuozlpKnkvJHmga/kuI3otrMifSx7InZhbHVlIjoic2V2ZXJlIiwibGFiZWwiOiLlpJrmlbDml7bpl7Tpmr7ku6XlhaXnnaHmiJblj43lpI3phpLmnaUifSx7InZhbHVlIjoidW5rbm93biIsImxhYmVsIjoi5LiN56Gu5a6aIC8g6L+R5pyf5rKh5pyJ5YGa6L+HIn1dfSx7ImlkIjoiZGFpbHlfZnVuY3Rpb24uZHJlc3NpbmciLCJsYWJlbCI6Iuepv+iho+OAgeepv+mei+OAgea0l+a8seaXtiIsInR5cGUiOiJjaG9pY2UiLCJyZXF1aXJlZCI6dHJ1ZSwiaGVscCI6IiIsIm9wdGlvbnMiOlt7InZhbHVlIjoibm9uZSIsImxhYmVsIjoi5rKh5pyJ5b2x5ZON77yM5ZKM5bmz5pe25LiA5qC3In0seyJ2YWx1ZSI6Im1pbGQiLCJsYWJlbCI6IuiDveeLrOeri+WujOaIkO+8jOS9huavlOi+g+aFouaIluS4jeiIkuacjSJ9LHsidmFsdWUiOiJtb2RlcmF0ZSIsImxhYmVsIjoi6YOo5YiG5Yqo5L2c5YGa5LiN5LqG77yM6ZyA6KaB6LCD5pW05pa55rOVIn0seyJ2YWx1ZSI6InNldmVyZSIsImxhYmVsIjoi6ZyA6KaB5Yir5Lq65Y2P5Yqp5omN6IO95a6M5oiQIn0seyJ2YWx1ZSI6InVua25vd24iLCJsYWJlbCI6IuS4jeehruWumiAvIOi/keacn+ayoeacieWBmui/hyJ9XX1dfSx7ImlkIjoiaGlzdG9yeSIsInRpdGxlIjoi5LqG6Kej5bey5pyJ5qOA5p+l5ZKM5aSE55CGIiwiZGVzY3JpcHRpb24iOiLkuI3orrDlvpfljLvlrablkI3np7DkuZ/msqHlhbPns7vvvIzlhpnkvaDnn6XpgZPnmoTmg4XlhrXljbPlj6/jgILkuI3loavlhpnkuI3mmK/igJzml6Dnl4Xlj7LigJ3jgIIiLCJmaWVsZHMiOlt7ImlkIjoiaGlzdG9yeV9zdGF0dXMiLCJsYWJlbCI6IuaYr+WQpuacieS4juiEiuafseacieWFs+eahOaXouW+gOiviuaWreOAgeWkluS8pOaIluaJi+acr++8nyIsInR5cGUiOiJjaG9pY2UiLCJyZXF1aXJlZCI6dHJ1ZSwiaGVscCI6IuS+i+Wmguabvuiiq+WRiuefpeS+p+W8r+OAgemqqOaKmOOAgemqqOi0qOeWj+advu+8jOaIluWBmui/h+iEiuafseaJi+acr+OAgiIsIm9wdGlvbnMiOlt7InZhbHVlIjoieWVzIiwibGFiZWwiOiLmnIkifSx7InZhbHVlIjoibm8iLCJsYWJlbCI6IuayoeaciSJ9LHsidmFsdWUiOiJ1bmtub3duIiwibGFiZWwiOiLkuI3noa7lrpoifV19LHsiaWQiOiJoaXN0b3J5IiwidHlwZSI6InRleHQiLCJsYWJlbCI6IuaXouW+gOaDheWGte+8iOacieeahOivneivt+eugOi/sO+8iSIsInJlcXVpcmVkIjpmYWxzZSwicmVxdWlyZWRfd2hlbiI6eyJmaWVsZCI6Imhpc3Rvcnlfc3RhdHVzIiwidmFsdWUiOiJ5ZXMifSwibWF4X2xlbmd0aCI6MjAwMCwiaGVscCI6IumAieaLqeKAnOacieKAneaXtuivt+ihpeWFheOAguWkp+amguaXtumXtOOAgeajgOafpeWQjeensOWSjOWMu+eUn+aAjuS5iOivtO+8m+S4jea4healmuWPr+WGmeKAnOS4jeiusOW+l+KAneOAgiJ9LHsiaWQiOiJ0cmVhdG1lbnRfc3RhdHVzIiwibGFiZWwiOiLnm67liY3mmK/lkKbmraPlnKjnlKjoja/jgIHmjqXlj5fmsrvnlpfmiJblgZrlurflpI3orq3nu4PvvJ8iLCJ0eXBlIjoiY2hvaWNlIiwicmVxdWlyZWQiOnRydWUsImhlbHAiOiIiLCJvcHRpb25zIjpbeyJ2YWx1ZSI6InllcyIsImxhYmVsIjoi5pyJIn0seyJ2YWx1ZSI6Im5vIiwibGFiZWwiOiLmsqHmnIkifSx7InZhbHVlIjoidW5rbm93biIsImxhYmVsIjoi5LiN56Gu5a6aIn1dfSx7ImlkIjoidHJlYXRtZW50IiwidHlwZSI6InRleHQiLCJsYWJlbCI6Iuato+WcqOi/m+ihjOeahOWkhOeQhu+8iOacieeahOivneivt+eugOi/sO+8iSIsInJlcXVpcmVkIjpmYWxzZSwicmVxdWlyZWRfd2hlbiI6eyJmaWVsZCI6InRyZWF0bWVudF9zdGF0dXMiLCJ2YWx1ZSI6InllcyJ9LCJtYXhfbGVuZ3RoIjoyMDAwLCJoZWxwIjoi6YCJ5oup4oCc5pyJ4oCd5pe26K+36KGl5YWF77yM5Y+v5Lul5YaZ6I2v5ZCN44CB6K6t57uD5pa55byP5Y+K5oSf6KeJ5piv5ZCm5pyJ5pWI77yb57O757uf5LiN5Lya5o2u5q2k6KaB5rGC5L2g5YGc6I2v5oiW5pS56I2v44CCIn0seyJpZCI6InNjb2xpb3Npc19oaXN0b3J5IiwibGFiZWwiOiLmmK/lkKbmm77nlLHljLvnlJ/noa7orqTohIrmn7HkvqflvK/vvJ8iLCJ0eXBlIjoiY2hvaWNlIiwicmVxdWlyZWQiOmZhbHNlLCJoZWxwIjoi5aSW6KeC5LiN5a+556ew5oiWIEFJIOe7k+aenOS4jeiDveabv+S7o+WMu+eUn+iviuaWreOAgiIsIm9wdGlvbnMiOlt7InZhbHVlIjoieWVzIiwibGFiZWwiOiLmnInvvIzljLvnlJ/noa7orqTov4cifSx7InZhbHVlIjoibm8iLCJsYWJlbCI6Iuayoeacieiiq+ehruiupOi/hyJ9LHsidmFsdWUiOiJ1bmtub3duIiwibGFiZWwiOiLkuI3noa7lrpoifV19LHsiaWQiOiJnb2FscyIsInR5cGUiOiJ0ZXh0IiwibGFiZWwiOiLov5nmrKHmnIDluIzmnJvlvpfliLDku4DkuYjluK7liqnvvJ8iLCJyZXF1aXJlZCI6dHJ1ZSwibWF4X2xlbmd0aCI6MTIwMCwiaGVscCI6IuS+i+Wmgu+8muaVtOeQhuWwseWMu+i1hOaWmeOAgeW8hOa4hemcgOimgeihpeWFheS7gOS5iOajgOafpeOAgeavlOi+g+WOhuasoeWPmOWMlu+8jOaIluiusOW9leeWvOeXm+S4juedoeecoOOAgiJ9XX1dfQ==";
    private OfflineQuestionnaire() {}

    public static void launch(Activity activity) {
        try { new Form(activity).open(); }
        catch (Exception e) { Toast.makeText(activity, "问卷加载失败", Toast.LENGTH_LONG).show(); }
    }

    private static final class Form {
        final Activity activity;
        final JSONObject schema;
        final JSONObject answers = new JSONObject();
        final SharedPreferences preferences;
        final int ink = Color.rgb(38,35,56), muted = Color.rgb(107,102,126), purple = Color.rgb(94,106,210);
        AlertDialog dialog;
        LinearLayout content;
        int step;
        boolean dirty;
        Form(Activity a) throws JSONException {
            activity = a;
            schema = new JSONObject(new String(Base64.decode(SCHEMA, Base64.DEFAULT), StandardCharsets.UTF_8));
            preferences = a.getSharedPreferences("offline_questionnaires", Activity.MODE_PRIVATE);
        }
        int dp(int n) { return (int)(n * activity.getResources().getDisplayMetrics().density + .5f); }
        TextView text(String s, int size, int color, boolean bold) {
            TextView t = new TextView(activity); t.setText(s); t.setTextSize(size); t.setTextColor(color);
            if (bold) t.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
            t.setPadding(0,dp(5),0,dp(5)); return t;
        }
        Button button(String label, Runnable action) {
            Button b = new Button(activity); b.setText(label); b.setTextColor(purple); b.setAllCaps(false);
            b.setOnClickListener(v -> action.run()); return b;
        }
        void open() {
            LinearLayout root = new LinearLayout(activity); root.setOrientation(LinearLayout.VERTICAL);
            root.setPadding(dp(16),0,dp(16),dp(8));
            ScrollView scroll = new ScrollView(activity); content = new LinearLayout(activity);
            content.setOrientation(LinearLayout.VERTICAL); scroll.addView(content);
            root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
            dialog = new AlertDialog.Builder(activity).setTitle("脊柱健康与日常功能问卷")
                .setView(root).setNegativeButton("关闭", (d,w) -> {})
                .setNeutralButton("上一步", (d,w) -> {})
                .setPositiveButton("下一步", (d,w) -> {}).create();
            dialog.setOnShowListener(d -> {
                Window window = dialog.getWindow(); if (window != null) window.setLayout(-1,-1);
                dialog.getButton(AlertDialog.BUTTON_NEGATIVE).setOnClickListener(v -> close());
                dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(v -> { if (step > 0) { step--; render(); scroll.scrollTo(0,0); } });
                dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
                    if (!valid()) return;
                    if (step + 1 < schema.optJSONArray("sections").length()) { step++; render(); scroll.scrollTo(0,0); }
                    else save();
                });
                render();
            });
            dialog.setCancelable(false); dialog.show();
        }
        Object get(String path) {
            String[] parts = path.split("\\.", 2);
            return parts.length == 1 ? answers.opt(parts[0]) : answers.optJSONObject(parts[0]) == null ? null : answers.optJSONObject(parts[0]).opt(parts[1]);
        }
        void put(String path, Object value) {
            try {
                String[] parts = path.split("\\.", 2);
                if (parts.length == 1) answers.put(parts[0], value);
                else { JSONObject nested = answers.optJSONObject(parts[0]); if (nested == null) { nested = new JSONObject(); answers.put(parts[0], nested); } nested.put(parts[1], value); }
                dirty = true;
            } catch (JSONException ignored) { Toast.makeText(activity, "无法保存该项", Toast.LENGTH_SHORT).show(); }
        }
        JSONObject section() { return schema.optJSONArray("sections").optJSONObject(step); }
        void render() {
            content.removeAllViews(); JSONObject section = section();
            content.addView(text("第 " + (step+1) + " / " + schema.optJSONArray("sections").length() + " 步 · " + section.optString("title"), 19, ink, true));
            content.addView(text(section.optString("description"), 14, muted, false));
            if (step == 0) {
                content.addView(text(schema.optString("notice"), 13, muted, false));
                content.addView(text("本机版仅保存本次回答；包含固定安全提示，评分、综合分析与网页同步尚未接入。", 13, purple, true));
                content.addView(button("查看已保存的问卷", this::history));
            }
            JSONArray fields = section.optJSONArray("fields");
            for (int i=0; i<fields.length(); i++) renderField(fields.optJSONObject(i));
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setEnabled(step > 0);
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setText(step+1 == schema.optJSONArray("sections").length() ? "保存回答" : "下一步");
        }
        void renderField(JSONObject field) {
            String id = field.optString("id"), type = field.optString("type");
            String label = "consent_confirmed".equals(id) ? "我同意将本次回答保存在本机，用于资料整理。" : field.optString("label");
            String help = "consent_confirmed".equals(id) ? "本问卷不是诊断。紧急不适时不要等待问卷；本机回答不会发送到外部模型。" : field.optString("help");
            content.addView(text(label + (field.optBoolean("required") ? " *" : ""), 16, ink, true));
            if (!help.isEmpty()) content.addView(text(help, 13, muted, false));
            JSONArray anchors = field.optJSONArray("anchors");
            if (anchors != null) for (int j=0;j<anchors.length();j++) { JSONObject a = anchors.optJSONObject(j); content.addView(text(a.optString("label") + "  " + a.optString("detail"),13,muted,false)); }
            Object existing = get(id);
            if ("choice".equals(type) || "score".equals(type)) {
                JSONArray options = field.optJSONArray("options");
                if ("score".equals(type)) { options = new JSONArray(); for (int n=0;n<=10;n++) { JSONObject o=new JSONObject(); try { o.put("value",n); o.put("label",String.valueOf(n)); } catch (JSONException ignored) {} options.put(o); } }
                RadioGroup group = new RadioGroup(activity);
                for (int j=0;j<options.length();j++) {
                    JSONObject o=options.optJSONObject(j); Object value=o.opt("value"); RadioButton b=new RadioButton(activity);
                    b.setText(o.optString("label")); b.setTextColor(ink); b.setPadding(0,dp(5),0,dp(5));
                    b.setChecked(value.equals(existing));
                    b.setOnClickListener(v -> {
                        put(id,value);
                        if (field.optInt("severity",0) >= 3 && "yes".equals(value))
                            new AlertDialog.Builder(activity).setTitle("请及时线下评估").setMessage("你选择了需要立即线下评估的信号。请前往急诊或联系当地急救服务，不要等待问卷完成或 AI 回复。")
                                .setPositiveButton("知道了",null).show();
                        else if ("adult_confirmed".equals(id) && "no".equals(value))
                            new AlertDialog.Builder(activity).setTitle("问卷适用范围").setMessage("当前问卷适用于成人。未满 18 周岁不能据此建立自动康复计划，请由监护人与专业人员协助评估。")
                                .setPositiveButton("知道了",null).show();
                    });
                    group.addView(b);
                }
                content.addView(group);
            } else if ("multi".equals(type)) {
                LinearLayout choices = new LinearLayout(activity); choices.setOrientation(LinearLayout.VERTICAL);
                JSONArray options=field.optJSONArray("options");
                for(int j=0;j<options.length();j++) {
                    JSONObject o=options.optJSONObject(j); String value=o.optString("value"); CheckBox box=new CheckBox(activity);
                    box.setText(o.optString("label")); box.setTextColor(ink); JSONArray selected=existing instanceof JSONArray?(JSONArray)existing:new JSONArray();
                    for(int k=0;k<selected.length();k++) if(value.equals(selected.optString(k))) box.setChecked(true);
                    box.setOnClickListener(v -> {
                        String exclusive=field.optString("exclusive");
                        if(box.isChecked() && !exclusive.isEmpty()) for(int k=0;k<choices.getChildCount();k++) {
                            CheckBox other=(CheckBox)choices.getChildAt(k); if(other!=box && (value.equals(exclusive) || exclusive.equals(other.getTag()))) other.setChecked(false);
                        }
                        JSONArray result=new JSONArray(); for(int k=0;k<choices.getChildCount();k++) { CheckBox c=(CheckBox)choices.getChildAt(k); if(c.isChecked()) result.put(c.getTag()); } put(id,result);
                    });
                    box.setTag(value); choices.addView(box);
                }
                content.addView(choices);
            } else if ("text".equals(type)) {
                EditText input=new EditText(activity); input.setText(existing instanceof String?(String)existing:""); input.setMinLines(2);
                input.setFilters(new InputFilter[]{new InputFilter.LengthFilter(field.optInt("max_length",1200))});
                input.addTextChangedListener(new TextWatcher(){public void beforeTextChanged(CharSequence s,int start,int count,int after){} public void onTextChanged(CharSequence s,int start,int before,int count){put(id,s.toString());} public void afterTextChanged(Editable e){} });
                content.addView(input);
            } else if ("consent".equals(type)) {
                CheckBox box=new CheckBox(activity); box.setText("同意保存本次回答"); box.setTextColor(ink); box.setChecked(Boolean.TRUE.equals(existing));
                box.setOnCheckedChangeListener((b,checked)->put(id,checked)); content.addView(box);
            }
        }
        boolean valid() {
            JSONArray fields=section().optJSONArray("fields");
            for (int i=0;i<fields.length();i++) {
                JSONObject f=fields.optJSONObject(i); JSONObject condition=f.optJSONObject("required_when");
                boolean required=f.optBoolean("required") || condition!=null && condition.optString("value").equals(get(condition.optString("field")));
                if (!required) continue;
                Object value=get(f.optString("id"));
                if(value==null || value==JSONObject.NULL || value instanceof String && ((String)value).trim().isEmpty() || value instanceof JSONArray && ((JSONArray)value).length()==0 || "consent".equals(f.optString("type")) && !Boolean.TRUE.equals(value)) {
                    Toast.makeText(activity, "consent_confirmed".equals(f.optString("id")) ? "请确认是否同意保存本次回答" : "请填写："+f.optString("label"),Toast.LENGTH_LONG).show(); return false;
                }
            }
            return true;
        }
        void save() {
            try {
                JSONArray history=new JSONArray(preferences.getString("history","[]"));
                if(history.length()>=100) { Toast.makeText(activity,"已达到本机问卷记录数量上限",Toast.LENGTH_LONG).show(); return; }
                JSONObject entry=new JSONObject(); entry.put("version",schema.optString("version"));
                entry.put("created_at",new SimpleDateFormat("yyyy-MM-dd HH:mm",Locale.CHINA).format(new Date())); entry.put("answers",new JSONObject(answers.toString()));
                history.put(entry);
                if(!preferences.edit().putString("history",history.toString()).commit()) { Toast.makeText(activity,"保存失败，请重试",Toast.LENGTH_LONG).show(); return; }
                dirty=false; dialog.dismiss(); new AlertDialog.Builder(activity).setTitle("回答已保存")
                    .setMessage("记录仅保存在这台设备。包含固定安全提示；本版本暂不提供综合分析、评分或网页同步。")
                    .setPositiveButton("完成",null).show();
            } catch (JSONException e) { Toast.makeText(activity,"本机记录无法读取，未覆盖原数据",Toast.LENGTH_LONG).show(); }
        }
        void history() {
            try {
                JSONArray items=new JSONArray(preferences.getString("history","[]"));
                if(items.length()==0){Toast.makeText(activity,"还没有问卷记录",Toast.LENGTH_SHORT).show(); return;}
                String[] labels=new String[items.length()]; for(int i=0;i<items.length();i++) labels[i]=items.optJSONObject(i).optString("created_at", "本机记录");
                new AlertDialog.Builder(activity).setTitle("已保存的问卷").setItems(labels,(d,index)->showSaved(items.optJSONObject(index), index))
                    .setNegativeButton("关闭",null).show();
            } catch(JSONException e){Toast.makeText(activity,"本机记录无法读取",Toast.LENGTH_LONG).show();}
        }
        void showSaved(JSONObject item, int index) {
            JSONObject saved=item.optJSONObject("answers"); if(saved==null)return;
            StringBuilder text=new StringBuilder("仅供回顾，未生成分析结果。\n\n");
            JSONArray sections=schema.optJSONArray("sections");
            for(int i=0;i<sections.length();i++){
                JSONObject section=sections.optJSONObject(i); text.append(section.optString("title")).append("\n"); JSONArray fields=section.optJSONArray("fields");
                for(int j=0;j<fields.length();j++){JSONObject f=fields.optJSONObject(j); String id=f.optString("id"); String[] parts=id.split("\\.",2); Object v=parts.length==1?saved.opt(parts[0]):saved.optJSONObject(parts[0])==null?null:saved.optJSONObject(parts[0]).opt(parts[1]); String label="consent_confirmed".equals(id)?"我同意将本次回答保存在本机，用于资料整理。":f.optString("label"); text.append(label).append("：").append(display(f,v)).append("\n");} text.append("\n");
            }
            new AlertDialog.Builder(activity).setTitle(item.optString("created_at")).setMessage(text.toString()).setPositiveButton("关闭",null)
                .setNegativeButton("删除", (d,w) -> new AlertDialog.Builder(activity).setTitle("删除这次问卷？")
                    .setMessage("删除后无法从本机恢复。") .setNegativeButton("取消",null)
                    .setPositiveButton("删除",(confirm,which)->deleteSaved(index)).show()).show();
        }
        void deleteSaved(int index) {
            try {
                JSONArray items=new JSONArray(preferences.getString("history","[]"));
                if(index<0 || index>=items.length()){Toast.makeText(activity,"记录不存在",Toast.LENGTH_SHORT).show();return;}
                items.remove(index);
                Toast.makeText(activity,preferences.edit().putString("history",items.toString()).commit()?"已删除":"删除状态不确定，请重开应用检查",Toast.LENGTH_LONG).show();
            } catch(JSONException e){Toast.makeText(activity,"本机记录无法读取，未删除",Toast.LENGTH_LONG).show();}
        }
        String display(JSONObject f,Object value) {
            if(value==null || value==JSONObject.NULL || value.toString().isEmpty())return "未填写";
            if(value instanceof Boolean)return Boolean.TRUE.equals(value)?"已同意保存":"未同意";
            if(value instanceof JSONArray){JSONArray arr=(JSONArray)value;StringBuilder s=new StringBuilder();for(int i=0;i<arr.length();i++){if(i>0)s.append("、");s.append(arr.optString(i));}return s.toString();}
            JSONArray opts=f.optJSONArray("options"); if(opts!=null)for(int i=0;i<opts.length();i++){JSONObject o=opts.optJSONObject(i);if(value.equals(o.opt("value")))return o.optString("label");}return value.toString();
        }
        void close() {
            if(!dirty){dialog.dismiss();return;}
            new AlertDialog.Builder(activity).setTitle("放弃本次填写？").setMessage("未保存的内容将被丢弃。")
                .setNegativeButton("继续填写",null).setPositiveButton("放弃",(d,w)->dialog.dismiss()).show();
        }
    }
}
