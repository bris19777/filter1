# התקנת הסוכן על מחשב Windows x86

הסוכן צריך לרוץ כשירות עם הרשאות מנהל כדי לתפוס פורט 53 ולקבוע את ה-DNS של המערכת.
כל השלבים נעשים פעם אחת בהתקנה, על ידי ההורה.

## 1. בניית קובץ הרצה (על מחשב הפיתוח)

```
pip install pyinstaller dnslib
pyinstaller --onefile --name filter1-agent agent.py
```

נוצר `dist\filter1-agent.exe`.

## 2. התקנה כשירות Windows

מעתיקים את ה-exe ל-`C:\Program Files\filter1\` ומתקינים כשירות עם NSSM
(https://nssm.cc) או `sc`:

```
sc create filter1 binPath= "C:\Program Files\filter1\filter1-agent.exe --server https://YOUR-SERVER --token YOUR-TOKEN" start= auto
sc description filter1 "filter1 parental control agent"
sc failure filter1 reset= 0 actions= restart/5000/restart/5000/restart/5000
sc start filter1
```

`failure ... actions= restart` גורם ל-Windows להפעיל את השירות מחדש אוטומטית אם הוא נעצר.

## 3. הערות

- הסוכן קובע את ה-DNS של הכרטיס הפעיל ל-`127.0.0.1` ומוודא זאת כל 30 שניות.
- כדי לעצור/להסיר צריך הרשאות מנהל, וקוד ההסרה שמופק בממשק הווב.
- השירות מופיע בשמו האמיתי `filter1` ב-`services.msc` — הכלי שקוף ולא מוסתר.

## הסרה

```
sc stop filter1
sc delete filter1
```
(דורש הרשאות מנהל; הפק קוד הסרה בממשק הווב לפני כן.)
