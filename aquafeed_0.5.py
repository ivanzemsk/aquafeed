import streamlit as st
import sqlite3
import json
import csv
import io
from pathlib import Path
from datetime import datetime, timezone

st.set_page_config(page_title="AquaFeed 0.5", layout="wide")
DB = Path(__file__).with_name("aquafeed.sqlite3")


def connect():
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events "
        "(id INTEGER PRIMARY KEY, created_at TEXT, basin TEXT, payload TEXT)"
    )
    return conn


DEMOS = {
    "Норма": dict(o2=8.0, tw=15.0, age=5, valid="да", react="обычная",
                  left="не замечены", equip="штатно"),
    "Отклонение": dict(o2=5.5, tw=19.0, age=5, valid="да", react="сниженная",
                        left="не замечены", equip="штатно"),
    "Нет данных": dict(o2=8.0, tw=15.0, age=30, valid="нет", react="обычная",
                        left="не замечены", equip="штатно"),
    "Авария": dict(o2=4.5, tw=15.0, age=5, valid="да", react="обычная",
                    left="не замечены", equip="авария"),
}
DEMO_LABELS = {
    "o2": "Кислород", "tw": "Температура воды", "age": "Возраст измерений",
    "valid": "Пригодность", "react": "Реакция", "left": "Остатки", "equip": "Оборудование",
}


def apply_demo(name):
    vals = DEMOS[name]
    changes = []
    for k, v in vals.items():
        old = st.session_state.get(k)
        if str(old) != str(v):
            changes.append(f"{DEMO_LABELS[k]}: {old if old not in (None, '') else 'пусто'} → {v}")
        st.session_state[k] = v
    st.session_state["confirm"] = True
    st.session_state["demo_diff"] = (
        "Изменено: " + ("; ".join(changes) if changes else "ничего, значения уже совпадали")
    )
    st.session_state.pop("result", None)
    st.session_state["share_touched"] = False


def init_state():
    defaults = dict(
        obj_type="УЗВ", place="в закрытом помещении", species="Форель", mass=0.0,
        goal="Товарное выращивание",
        o2=0.0, tw=0.0, age=0, valid="", react="обычная", left="не замечены", equip="штатно",
        wx="неизвестно", wind="неизвестно", trend="неизвестно", hpa=0.0, ta=0.0, wcom="",
        daily=0.0, portion=0.0, given=0.0, src="План рыбовода",
        o2min=6.0, tmin=12.0, tmax=18.0, maxage=15, confirm=False, approver="",
        eqH=False, eqC=False, eqO=False,
        pct_input=0.0, kg_input=0.0, share_mode="в процентах", share_touched=False,
        decision="не выбрано", basis="", comment="",
        demo_diff="",
    )
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def fmt(n, d=2):
    if n is None:
        return "—"
    try:
        return f"{float(n):,.{d}f}".replace(",", " ").replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def round2(n):
    return round(float(n), 2)


def nz(v):
    """0/None считаем «не введено» для полей, где 0 не несёт смысла."""
    return v if v not in (None, 0, 0.0) else None


init_state()

st.title("AquaFeed 0.5")
st.caption("Прототип. Решение принимает рыбовод")

PLACE_OPTIONS = ["в закрытом помещении", "под навесом или частично закрыт", "под открытым небом"]

# ================= САЙДБАР: профиль и настройки =================
with st.sidebar:
    st.header("Профиль и настройки")
    st.subheader("Объект")
    st.selectbox("Тип объекта", ["УЗВ", "Пруд", "Садки"], key="obj_type")
    st.selectbox("Размещение", PLACE_OPTIONS, key="place")
    if st.session_state.place == "в закрытом помещении":
        st.caption("Осадки и наружный ветер не используются в оценке.")
        weather_enabled = False
        if st.session_state.wx != "неизвестно":
            st.session_state.wx = "неизвестно"
        if st.session_state.wind != "неизвестно":
            st.session_state.wind = "неизвестно"
    else:
        weather_enabled = True
        if st.session_state.place == "под навесом или частично закрыт":
            st.caption("Навес не считается равнозначным помещению.")
    st.text_input("Вид рыбы", key="species")
    st.number_input("Средняя масса рыбы, г", min_value=0.0, step=10.0, key="mass")
    st.selectbox("Цель выращивания",
                 ["Товарное выращивание", "Маточное стадо / икра", "Молодь / воспроизводство"],
                 key="goal")

    st.divider()
    st.subheader("Рабочие диапазоны")
    st.number_input("Минимум кислорода, мг/л", min_value=0.0, step=0.1, key="o2min")
    st.number_input("Минимум температуры воды, °C", step=0.5, key="tmin")
    st.number_input("Максимум температуры воды, °C", step=0.5, key="tmax")
    st.number_input("Допустимый возраст измерений, мин", min_value=0, step=1, key="maxage")
    st.checkbox("Рабочие диапазоны проверены и подтверждены", key="confirm")
    st.text_input("Кто согласовал / источник (необязательно)", key="approver")
    st.caption("Для продолжения достаточно галочки выше; поле не обязательно для заполнения.")

    st.divider()
    st.subheader("Оборудование: доступные средства")
    st.checkbox("Есть подогрев", key="eqH")
    st.checkbox("Есть охлаждение", key="eqC")
    st.checkbox("Есть подача кислорода", key="eqO")
    st.caption("Значения диапазонов по умолчанию — примеры, не проверенные нормы для хозяйства.")

# ================= Демо-сценарии =================
with st.expander("Демонстрационные сценарии"):
    dcols = st.columns(4)
    for col, name in zip(dcols, DEMOS):
        col.button(name, on_click=apply_demo, args=(name,), use_container_width=True)
    st.caption("Тестовые сценарии, а не рабочие режимы. Границы диапазонов не меняются; "
               "флажок подтверждения диапазонов ставится в «да». После выбора сразу "
               "выполняется оценка.")
    if st.session_state.demo_diff:
        st.info(st.session_state.demo_diff)

with st.expander("О роли рыбовода и ограничениях"):
    st.caption(
        "Нет подключения к датчикам, внешней погоде и оборудованию; все данные вводятся "
        "вручную. Нет автоматического кормления и расчёта времени до гибели. Статус "
        "«отклонений не обнаружено» не гарантирует безопасность. Решение и фактическую "
        "выдачу корма определяет рыбовод."
    )

# ================= Основные блоки =================
col_meas, col_wx, col_feed = st.columns(3)

with col_meas:
    st.subheader("Измерения")
    st.number_input("Кислород, мг/л", min_value=0.0, step=0.1, key="o2")
    st.number_input("Температура воды, °C", step=0.5, key="tw")
    st.number_input("Возраст измерений, мин", min_value=0, step=1, key="age")
    st.selectbox("Пригодность показаний", ["", "да", "нет"], key="valid")
    st.selectbox("Реакция на корм", ["обычная", "сниженная", "отказ от корма"], key="react")
    st.selectbox("Остатки корма", ["не замечены", "замечены"], key="left")
    st.selectbox("Оборудование", ["штатно", "авария"], key="equip")

with col_wx:
    st.subheader("Погода и давление")
    st.selectbox("Погода", ["неизвестно", "ясно", "облачно", "дождь", "снег", "гроза/шторм"],
                 key="wx", disabled=not weather_enabled)
    st.selectbox("Ветер", ["неизвестно", "штиль", "слабый", "умеренный", "сильный"],
                 key="wind", disabled=not weather_enabled)
    st.selectbox("Изменение давления",
                 ["неизвестно", "стабильно", "растёт", "снижается", "резко растёт", "резко снижается"],
                 key="trend")
    st.number_input("Давление, гПа (необязательно)", step=1.0, key="hpa")
    st.number_input("Температура воздуха, °C", step=0.5, key="ta")
    st.text_input("Комментарий (необязательно)", key="wcom")
    st.caption("Погода и давление — контекст. Они не назначают процент корма.")

with col_feed:
    st.subheader("Кормление")
    st.number_input("Суточный план, кг", min_value=0.0, step=0.5, key="daily")
    st.number_input("Плановая разовая порция, кг", min_value=0.0, step=0.1, key="portion")
    st.number_input("Уже выдано за сутки, кг", min_value=0.0, step=0.5, key="given")
    st.selectbox("Источник кормовой нормы",
                 ["План рыбовода", "Таблица производителя", "Демонстрационный пример"], key="src")

st.divider()
st.subheader("Результат")


def current_signature():
    keys = ["obj_type", "place", "species", "mass", "goal", "o2", "tw", "age", "valid",
            "react", "left", "equip", "wx", "wind", "trend", "hpa", "ta", "wcom",
            "o2min", "tmin", "tmax", "maxage", "confirm", "eqH", "eqC", "eqO", "src"]
    return json.dumps({k: st.session_state.get(k) for k in keys}, sort_keys=True, default=str)


def evaluate():
    items = []  # (group, level, text, now, limit, action)

    def add(g, l, t, now, lim, act):
        items.append(dict(g=g, l=l, t=t, now=now, lim=lim, act=act))

    o2 = nz(st.session_state.o2)
    tw = st.session_state.tw
    age = nz(st.session_state.age)
    o2min = st.session_state.o2min
    tmin, tmax = st.session_state.tmin, st.session_state.tmax
    maxage = nz(st.session_state.maxage)
    hasO2 = st.session_state.eqO
    hasH = st.session_state.eqH
    hasC = st.session_state.eqC

    # --- готовность настроек ---
    if not st.session_state.confirm:
        add("set", "info", "Подтвердите рабочие диапазоны в настройках",
            "галочка не поставлена", "нужна галочка подтверждения",
            "Откройте боковую панель «Рабочие диапазоны», проверьте значения и поставьте "
            "галочку. Это вопрос настроек, а не состояния рыбы.")
    ranges_ok = tmin is not None and tmax is not None and tmin < tmax and o2min is not None
    if not ranges_ok:
        add("set", "info", "Рабочие диапазоны заданы не полностью или некорректно",
            f"O₂ мин {fmt(o2min)}, t мин {fmt(tmin)}, t макс {fmt(tmax)}",
            "температура: минимум меньше максимума",
            "Заполните минимум кислорода и корректные границы температуры.")

    # --- качество данных ---
    missing = []
    if o2 is None:
        missing.append("кислород")
    if age is None:
        missing.append("возраст измерений")
    if missing:
        add("data", "info", "Не хватает данных: " + ", ".join(missing),
            "не введено", "—", "Введите недостающие значения.")
    if st.session_state.valid != "да":
        add("data", "info", "Не подтверждена пригодность показаний кислорода и температуры",
            st.session_state.valid or "не указано", "нужно «да»",
            "Проверьте датчики или выполните ручное измерение и отметьте пригодность.")
    fresh = age is not None and maxage is not None and age <= maxage
    if age is not None and maxage is not None and age > maxage:
        add("data", "info", f"Измерениям {age} мин. В настройках допускается не более "
                             f"{maxage} мин.", f"{age} мин", f"не более {maxage} мин",
            "Обновите измерения.")
    data_ok = not missing and st.session_state.valid == "да" and fresh

    # --- состояние объекта ---
    if st.session_state.equip == "авария":
        add("obj", "crit", "Авария оборудования", "авария", "штатная работа",
            "Действуйте по регламенту объекта и уведомите ответственного.")

    if data_ok and ranges_ok and st.session_state.confirm:
        if o2 < o2min:
            act = "Проверьте аэрацию и оборудование."
            act += (" Подача кислорода доступна — используйте её по регламенту объекта."
                    if hasO2 else " Подача кислорода не отмечена как доступная: "
                                  "согласуйте действия с рыбоводом.")
            add("obj", "warn", f"Кислород {fmt(o2)} мг/л — ниже рабочего минимума "
                                f"{fmt(o2min)} мг/л", f"{fmt(o2)} мг/л",
                f"минимум {fmt(o2min)} мг/л", act)
        if tw < tmin:
            act = ("Проверьте подогрев; изменение температуры — постепенное, по "
                   "регламенту объекта." if hasH else
                   "Подогрев не отмечен как доступный: обсудите действия с рыбоводом.")
            add("obj", "warn", f"Температура воды {fmt(tw)} °C вне рабочего диапазона "
                                f"{fmt(tmin)}–{fmt(tmax)} °C (ниже)", f"{fmt(tw)} °C",
                f"{fmt(tmin)}–{fmt(tmax)} °C", act)
        if tw > tmax:
            act = ("Проверьте охлаждение; изменение — постепенное, по регламенту."
                   if hasC else "Охлаждение не отмечено как доступное: согласуйте "
                                 "действия с рыбоводом.") + " Контролируйте кислород."
            add("obj", "warn", f"Температура воды {fmt(tw)} °C вне рабочего диапазона "
                                f"{fmt(tmin)}–{fmt(tmax)} °C (выше)", f"{fmt(tw)} °C",
                f"{fmt(tmin)}–{fmt(tmax)} °C", act)

    if st.session_state.react == "сниженная":
        add("obj", "warn", "Вы отметили: сниженная реакция на корм", "сниженная", "обычная",
            "Причина не установлена. Оценку и решение по кормлению даёт рыбовод.")
    if st.session_state.react == "отказ от корма":
        add("obj", "warn", "Вы отметили: отказ от корма", "отказ от корма", "обычная",
            "Причина не установлена. Оценку и решение по кормлению даёт рыбовод.")
    if st.session_state.left == "замечены":
        add("obj", "warn", "Вы отметили: замечены остатки корма", "замечены", "не замечены",
            "Проверьте остатки и согласуйте объём следующей выдачи с рыбоводом.")

    if st.session_state.place != "в закрытом помещении" and (
        st.session_state.wx == "гроза/шторм" or st.session_state.wind == "сильный"
    ):
        kind = "открытого" if st.session_state.place == "под открытым небом" else "частично укрытого"
        add("obj", "warn", f"Неблагоприятные погодные условия для {kind} объекта",
            f"погода: {st.session_state.wx}; ветер: {st.session_state.wind}",
            "гроза/шторм или сильный ветер",
            "Проверьте безопасность персонала и оборудования.")

    if any(x["l"] == "crit" for x in items):
        status, cls = "Критическая тревога", "crit"
    elif items:
        if not data_ok or not ranges_ok or not st.session_state.confirm:
            status, cls = "Недостаточно данных", "nd"
        else:
            status, cls = "Требуется внимание", "warn"
    else:
        status, cls = "Отклонений по доступным данным не обнаружено", "ok"

    blocked = status == "Критическая тревога" or not data_ok or not ranges_ok \
        or not st.session_state.confirm

    st.session_state.result = dict(
        sig=current_signature(), status=status, cls=cls, items=items, blocked=blocked,
        time=datetime.now(timezone.utc).isoformat(),
    )
    st.session_state.share_touched = False
    st.session_state.pct_input = 0.0
    st.session_state.kg_input = 0.0


st.button("Оценить", type="primary", on_click=evaluate, use_container_width=True)

STATUS_RENDER = {"crit": st.error, "nd": st.info, "warn": st.warning, "ok": st.success}
GROUP_NAMES = {"obj": "Состояние объекта", "data": "Качество данных", "set": "Готовность настроек"}

result = st.session_state.get("result")
if not result:
    st.caption("Заполните данные и нажмите «Оценить».")
else:
    stale = result["sig"] != current_signature()
    if stale:
        st.warning("Данные изменились после оценки. Нажмите «Оценить» повторно.")
    STATUS_RENDER[result["cls"]](result["status"])
    if result["cls"] == "ok":
        st.caption("Это не гарантия безопасности: отклонения не найдены только по "
                   "введённым данным.")
    for g in ["obj", "data", "set"]:
        group_items = [x for x in result["items"] if x["g"] == g]
        st.markdown(f"**{GROUP_NAMES[g]}**")
        if not group_items:
            st.caption("Замечаний нет.")
        else:
            for x in group_items:
                st.markdown(
                    f"- **{x['t']}**  \n"
                    f"  Сейчас: {x['now']} · Граница: {x['lim']}  \n"
                    f"  Действие: {x['act']}"
                )
    ctx = []
    if st.session_state.place != "в закрытом помещении":
        if st.session_state.wx != "неизвестно":
            ctx.append("погода: " + st.session_state.wx)
        if st.session_state.wind != "неизвестно":
            ctx.append("ветер: " + st.session_state.wind)
    if st.session_state.trend != "неизвестно":
        ctx.append("давление: " + st.session_state.trend)
    if nz(st.session_state.hpa):
        ctx.append(fmt(st.session_state.hpa, 1) + " гПа")
    if nz(st.session_state.ta):
        ctx.append("воздух " + fmt(st.session_state.ta, 1) + " °C")
    if ctx:
        st.caption("Контекст (не меняет рацион): " + " · ".join(ctx))

    # ---------------- Объём корма ----------------
    st.subheader("Объём корма")
    if stale:
        st.warning("Расчёт доли недоступен: оцените заново.")
    elif result["blocked"]:
        if result["status"] == "Критическая тревога":
            st.error("Расчёт доли заблокирован: критическая тревога. Следуйте регламенту объекта.")
        else:
            st.warning("Расчёт доли заблокирован: нет актуальных данных или настроек. "
                       "Корм не назначается автоматически, в том числе «половина».")
    else:
        P = float(st.session_state.portion or 0.0)
        D = float(st.session_state.daily or 0.0)
        G = float(st.session_state.given or 0.0)
        zero_portion = P <= 0

        sc = st.columns(4)
        sc[0].metric("Суточный план, кг", fmt(D))
        sc[1].metric("Плановая разовая порция, кг", fmt(P))
        sc[2].metric("Уже выдано за сутки, кг", fmt(G))

        def set_share_chip(p):
            st.session_state.share_mode = "в процентах"
            st.session_state.pct_input = float(p)
            st.session_state.kg_input = round2(P * p / 100) if P > 0 else 0.0
            st.session_state.share_touched = True

        st.caption("Выберите долю, или введите своё значение ниже — в процентах либо в кг.")
        chip_cols = st.columns(5)
        for c, p in zip(chip_cols, [0, 25, 50, 75, 100]):
            c.button(f"{p}%", key=f"chip_{p}", disabled=zero_portion,
                     use_container_width=True, on_click=set_share_chip, args=(p,))

        pct, kg = None, None

        if zero_portion:
            st.info("Плановая разовая порция равна нулю или не задана — процент не "
                    "рассчитывается. Можно ввести объём вручную в кг.")

            def on_kg_only_change():
                st.session_state.share_touched = True

            kg_val = st.number_input("Объём вручную, кг", min_value=0.0, step=0.1,
                                      key="kg_input", on_change=on_kg_only_change)
            kg = kg_val if st.session_state.share_touched else None
        else:
            def on_pct_change():
                st.session_state.share_mode = "в процентах"
                st.session_state.kg_input = round2(P * st.session_state.pct_input / 100)
                st.session_state.share_touched = True

            def on_kg_change():
                st.session_state.share_mode = "в кг"
                st.session_state.pct_input = round2(st.session_state.kg_input / P * 100)
                st.session_state.share_touched = True

            st.radio("Способ ввода доли", ["в процентах", "в кг"], horizontal=True,
                      key="share_mode")
            ic1, ic2 = st.columns(2)
            with ic1:
                st.number_input(
                    "Своё значение, %", min_value=0.0, step=1.0, key="pct_input",
                    disabled=st.session_state.share_mode != "в процентах",
                    on_change=on_pct_change,
                )
            with ic2:
                st.number_input(
                    "Объём, кг", min_value=0.0, step=0.1, key="kg_input",
                    disabled=st.session_state.share_mode != "в кг",
                    on_change=on_kg_change,
                )
            if st.session_state.share_touched:
                pct = float(st.session_state.pct_input)
                kg = float(st.session_state.kg_input)

        if kg is None:
            st.markdown("**Порция не выбрана**")
        elif zero_portion:
            st.markdown(f"**Выбрано {fmt(kg)} кг. Процент не рассчитывается: "
                        f"плановая разовая порция равна нулю.**")
        elif pct is not None and pct > 100:
            st.markdown(f":red[**Выбрано {fmt(kg)} кг ({fmt(pct,1)}% плановой порции). "
                        f"Превышение плановой порции: +{fmt(kg-P)} кг**]")
        else:
            st.markdown(f"**Дать {fmt(pct,1)}% плановой порции — {fmt(kg)} кг**")

        st.selectbox("Решение оператора",
                     ["не выбрано", "дать выбранный объём", "отложить кормление",
                      "проверить оборудование", "другое"], key="decision")
        st.text_input("Основание корректировки", key="basis")
        st.text_input("Комментарий", key="comment")
        st.caption("Сохранение решения не означает, что корм фактически выдан.")

    if st.button("Сохранить в журнал", disabled=stale):
        chosen_pct = chosen_kg = None
        if not result["blocked"]:
            P = float(st.session_state.portion or 0.0)
            if st.session_state.share_touched:
                chosen_kg = float(st.session_state.kg_input)
                chosen_pct = float(st.session_state.pct_input) if P > 0 else None
        rec = dict(
            time=result["time"], status=result["status"],
            placement=st.session_state.place, type=st.session_state.obj_type,
            species=st.session_state.species, mass_g=st.session_state.mass,
            goal=st.session_state.goal,
            o2=st.session_state.o2, water_t=st.session_state.tw, age_min=st.session_state.age,
            valid=st.session_state.valid, reaction=st.session_state.react,
            leftovers=st.session_state.left, equipment=st.session_state.equip,
            weather=st.session_state.wx, wind=st.session_state.wind,
            pressure_trend=st.session_state.trend, pressure_hpa=st.session_state.hpa,
            air_t=st.session_state.ta, weather_comment=st.session_state.wcom,
            reasons=" | ".join(x["t"] for x in result["items"]),
            norm_source=st.session_state.src, daily_plan_kg=st.session_state.daily,
            planned_portion_kg=st.session_state.portion, given_kg=st.session_state.given,
            ranges_confirmed=st.session_state.confirm, approver=st.session_state.approver,
            chosen_pct=chosen_pct, chosen_kg=chosen_kg,
            basis=st.session_state.get("basis", ""),
            decision=st.session_state.get("decision", "не выбрано"),
            comment=st.session_state.get("comment", ""),
        )
        with connect() as conn:
            conn.execute(
                "INSERT INTO events(created_at,basin,payload) VALUES (?,?,?)",
                (datetime.now(timezone.utc).isoformat(), st.session_state.obj_type,
                 json.dumps(rec, ensure_ascii=False)),
            )
        st.success("Сохранено локально. Это запись решения, не подтверждение "
                   "фактической выдачи корма.")

st.divider()
st.subheader("Журнал")
with connect() as conn:
    rows = conn.execute(
        "SELECT id, created_at, payload FROM events ORDER BY id DESC LIMIT 100"
    ).fetchall()

if rows:
    records = [dict(json.loads(row[2]), id=row[0], saved_at_utc=row[1]) for row in rows]
    cols_show = ["id", "saved_at_utc", "status", "placement", "chosen_kg", "chosen_pct", "decision"]
    st.dataframe([{k: rec.get(k) for k in cols_show} for rec in records],
                 use_container_width=True)

    keys = list(dict.fromkeys(k for rec in records for k in rec))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=keys, delimiter=";")
    writer.writeheader()
    for rec in records:
        writer.writerow({k: (json.dumps(v, ensure_ascii=False)
                              if isinstance(v, (dict, list)) else v)
                          for k, v in rec.items()})
    st.download_button("Выгрузить CSV", out.getvalue().encode("utf-8-sig"),
                        "aquafeed_log.csv", "text/csv")
else:
    st.caption("Записей пока нет.")

st.caption(
    "Локальный прототип без авторизации: не размещайте производственные данные публично. "
    "Нет датчиков, внешних API погоды, модели роста и расчёта времени до аварии. "
    "Журнал хранится рядом с приложением в aquafeed.sqlite3."
)
