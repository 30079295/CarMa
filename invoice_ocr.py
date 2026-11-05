import re
import sys
from pathlib import Path
from datetime import datetime
from statistics import median


#TEXT HELPERS

def clean_text(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).strip())

def normalize_text(text):
    text = clean_text(text).lower()
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        text
    ).strip()

#OCR POSITION HELPERS

def get_box_info(box):
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    left = min(xs)
    right = max(xs)
    top = min(ys)
    bottom = max(ys)
    return {
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "x": (left + right) / 2,
        "y": (top + bottom) / 2,
        "width": max(1.0, right - left),
        "height": max(1.0, bottom - top)
    }

def estimate_page_tilt(result):
    slopes = []
    for box in result["rec_polys"]:
        try:
            x1 = float(box[0][0])
            y1 = float(box[0][1])
            x2 = float(box[1][0])
            y2 = float(box[1][1])
            width = x2 - x1
            if abs(width) < 20:
                continue
            slope = (y2 - y1) / width
            if abs(slope) < 0.5:
                slopes.append(slope)
        except Exception:
            continue
    return median(slopes) if slopes else 0.0

def create_items(result):
    items = []
    tilt = estimate_page_tilt(result)
    for text, box in zip(result["rec_texts"], result["rec_polys"]):
        text = clean_text(text)
        if not text:
            continue
        info = get_box_info(box)
        corrected_y = info["y"] - tilt * info["x"]
        items.append({
            "text": text,
            "normalized": normalize_text(text),
            "x": info["x"],
            "y": info["y"],
            "corrected_y": corrected_y,
            "left": info["left"],
            "right": info["right"],
            "top": info["top"],
            "bottom": info["bottom"],
            "width": info["width"],
            "height": info["height"]
        })
    return items

#REBUILD OCR INTO LINES

def join_lines(result):
    items = create_items(result);

    if not items:
        return []
    median_height = median(item["height"] for item in items)
    tolerance = max(6, median_height * 0.65)
    rows = []
    for item in sorted(items, key=lambda i: i["corrected_y"]):
        best_row = None
        best_distance = None
        for row in rows:
            distance = abs(item["corrected_y"] - row["y"])
            if distance <= tolerance:
                if best_distance is None or distance < best_distance:
                    best_row = row
                    best_distance = distance
        if best_row is None:
            rows.append({
                "y": item["corrected_y"],
                "items": [item]
            })
        else:
            best_row["items"].append(item)
            best_row["y"] = (
                sum(i["corrected_y"] for i in best_row["items"])
                / len(best_row["items"])
            )
    lines = []
    for row in sorted(rows, key=lambda r: r["y"]):
        row_items = sorted(row["items"], key=lambda i: i["x"])
        line = clean_text(" ".join(i["text"] for i in row_items))
        if line:
            lines.append(line)
    return lines

#COMPANY NAME

def get_company_from_results(results, fallback_lines):
    business_words = (
        r"automotive|motoring|motors?|auto|garage|tyres?|tires?|"
        r"mechanical|mechanics?|workshop|engineering|panelbeaters?|"
        r"panel beaters?|collision|repairs?|car care|car clinic|services?|"
        r"service centre|service center|ltd|limited"
    )


    bad_words = (
        r"number|email|phone|mobile|fax|gst|invoice|estimate|quote|receipt|tax|"
        r"date|customer|vehicle|registration|odometer|description|quantity|"
        r"qty|price|amount|subtotal|total|balance|paid|fictional|ocr testing|"
        r"sample|website|account|bank|bill to|invoice to"
    )

    candidates = []

    def trim_company_name(text):
        text = clean_text(text)

        #Remove invoice/quote labels accidentally joined onto
        #the front of the company name by OCR.
        text = re.sub(
            r"^(?:valid until|expiry|quote date|invoice date|"
            r"estimate date|customer ref|page)\s*:?\s*",
            "",
            text,
            flags=re.I
        )

        #Fix OCR joining the final word and LTD, e.g. AUTOMOTIVELTD.
        text = re.sub(
            r"(?i)([A-Za-z])(?:ltd)\b",
            r"\1 LTD",
            text
        )

        #Remove a single service/tagline word that OCR may place
        #immediately before the real company name.
        if re.search(
            r"\b(?:ltd|limited)\b",
            text,
            re.I
        ):
            text = re.sub(
                r"^(?:service|services|repairs?|diagnostics?|wof|servicing|parts)\s+",
                "",
                text,
                flags=re.I
            )

        #If the company name contains LTD / LIMITED, ignore any
        #slogan or tagline after it.
        match = re.search(
            r"^(.*?\b(?:ltd|limited)\b)",
            text,
            re.I
        )

        if match:
            return clean_text(
                match.group(1)
            )

        return text

    def valid_name_piece(text):
        text = trim_company_name(text)

        if not text:
            return False

        #A one or two-letter OCR fragment is too uncertain to use
        #as a supplier name unless it contains a business name word.
        if len(re.sub(r"[^A-Za-z]", "", text)) <= 2 and not re.search(
            business_words, text, re.I
        ):
            return False

        if "@" in text:
            return False

        if re.search(r"\bwww\.|https?://|\.(?:co\.nz|com|net|org|nz)\b", text, re.I):
            return False

        if re.search(bad_words, text, re.I):
            return False

        if re.match(r"^\d+[A-Za-z]?\s", text):
            return False

        if len(text) > 80:
            return False

        if len(re.findall(r"\d", text)) > 3:
            return False

        return True

    def score_name(name, relative_y, combined=False, font_ratio=1.0):

        name = trim_company_name(name)

        if not valid_name_piece(name):
            return -999

        score = 0

        if re.search(
            business_words,
            name,
            re.I
        ):
            score += 30

        #Strong preference for registered company names.
        if re.search(
            r"\b(?:ltd|limited)\b",
            name,
            re.I
        ):
            score += 25

        #Detect lines that look like service/tagline lists.
        tagline_words = (
            r"\bdiagnostics\b|"
            r"\bservicing\b|"
            r"\brepairs?\b|"
            r"\bwof\b|"
            r"\bpre[\s\-]?checks?\b|"
            r"\bauto electrical\b|"
            r"\bmechanical repairs?\b"
        )

        tagline_hits = len(
            re.findall(
                tagline_words,
                name,
                re.I
            )
        )

        #A line containing several workshop services is
        #probably a slogan, not the company name.
        if tagline_hits >= 2:
            score -= 45

        #Avoid combining a brand or slogan with another company name.
        if combined and re.search(
            r"\bsuper shoppes\b",
            name,
            re.I
            ):
            score -= 25

        #Reduce the score of advertising slogans.
        advertising_words = (
            r"\bpremier\b|\bleading\b|\btrusted\b|"
            r"\bnationwide\b|\bnz's\b.*\bcentres\b"
        )
        
        if re.search(advertising_words, name, re.I):
            score -= 60

        if relative_y < 0.15:
            score += 15
        elif relative_y < 0.30:
            score += 10

        if 2 <= len(name.split()) <= 7:
            score += 6

        if name.isupper():
            score += 4

        
        #Prefer single-box names by default, but reward
        #combined names that add meaningful business identity.
        if combined:
            score -= 8

        #Reduce combined names starting with a generic workshop word.
        if re.match(
            r"^(?:automotive|mechanical|repairs?|services?)\b",
            name,
            re.I
            ):
            
            score -= 15

            words = name.split()

            #A longer combined name is more likely to be
            #the complete business name than a short fragment.
            if len(words) >= 3:
                score += 12


      
        #Reduce the score of generic workshop taglines.
        #Legitimate company names remain eligible.
        generic_words = {
            "service", "services", "repair", "repairs",
            "diagnostic", "diagnostics", "wof",
            "maintenance", "servicing", "parts"
        }

        name_words = re.findall(r"[a-z]+", name.lower())

        if (
            name_words
            and all(word in generic_words for word in name_words)
            and not re.search(r"\b(?:ltd|limited)\b", name, re.I)
        ):
            score -= 35

        #Reward larger text relative to the invoice's
        #typical OCR text height.
        if font_ratio >= 1.8:
            score += 12
        elif font_ratio >= 1.4:
            score += 7
        elif font_ratio >= 1.2:
            score += 3

        return score



    for result in results:
        items = create_items(result)

        if not items:
            continue

        page_height = max(
            item["bottom"]
            for item in items
        )

        page_width = max(
            item["right"]
            for item in items
        )

        median_height = median(
            item["height"]
            for item in items
        )

        header_items = [
            item
            for item in items
            if item["y"] / page_height < 0.35
            and valid_name_piece(item["text"])
        ]

        header_items.sort(
            key=lambda item: (
                item["corrected_y"],
                item["x"]
            )
        )

        #A logo can separate its brand and business type into different boxes.
        #Combine only close, similarly aligned fragments in the top header.
        for brand in header_items:
            brand_text = clean_text(brand["text"])
            if not re.fullmatch(r"[A-Za-z]{3,20}", brand_text):
                continue
            if re.search(business_words, brand_text, re.I):
                continue
            if brand["y"] / page_height > 0.20:
                continue
            for other in header_items:
                if brand is other:
                    continue
                other_text = clean_text(other["text"])
                if not re.search(r"\b(?:transmission specialists?|brake\s*&\s*suspension|mechanical|automotive|motors?|garage|tyre centre)\b", other_text, re.I):
                    continue
                if re.search(r"\b(?:automatic|manual|rebuilds|servicing|diagnostics|wof)\b", other_text, re.I):
                    continue
                gap = other["corrected_y"] - brand["corrected_y"]
                if gap < -median_height or gap > page_height * 0.11:
                    continue
                if abs(other["left"] - brand["left"]) > page_width * 0.30:
                    continue
                combined_name = trim_company_name(brand_text + " " + other_text)
                candidates.append({
                    "name": combined_name,
                    "score": score_name(combined_name, min(brand["y"], other["y"]) / page_height) + 28,
                    "y": min(brand["y"], other["y"])
                })

        #Single-line company names
        for item in header_items:
            name = trim_company_name(
                item["text"]
            )

            relative_y = (
                item["y"]
                / page_height
            )

            candidates.append({
                "name": name,
                "score": score_name(
                    name,
                    relative_y,
                    font_ratio=item["height"] / max(1.0, median_height)
                ),
                "y": item["y"]
            })

        #Split company names such as:
        #NORTH SHORE
        #AUTO SERVICES
        for index, first in enumerate(header_items):
            for second in header_items[index + 1:]:

                vertical_distance = (
                    second["corrected_y"]
                    - first["corrected_y"]
                )

                if vertical_distance < 0:
                    continue

                if vertical_distance > page_height * 0.09:
                    continue

                horizontal_distance = abs(
                    first["x"]
                    - second["x"]
                )

                left_difference = abs(
                    first["left"]
                    - second["left"]
                )

                aligned = (
                    horizontal_distance < page_width * 0.25
                    or left_difference < page_width * 0.15
                )

                if not aligned:
                    continue

                combined = clean_text(
                    first["text"]
                    + " "
                    + second["text"]
                )

                combined = trim_company_name(
                    combined
                )

                if not re.search(
                    business_words,
                    combined,
                    re.I
                ):
                    continue

                relative_y = (
                    min(
                        first["y"],
                        second["y"]
                    )
                    / page_height
                )

                combined_score = score_name(
                    combined,
                    relative_y,
                    combined=True,
                    font_ratio=max(first["height"], second["height"])
                    / max(1.0, median_height)
                )

                #Names in a large, two-line logo often have the brand
                #on the first line and the business type on the second.
                #Reward that layout, not any particular business name.
                close_lines = vertical_distance < max(median_height * 3.5, page_height * 0.055)
                large_letters = (first["height"] >= median_height * 1.3
                                 and second["height"] >= median_height * 1.3)
                brand_then_type = (
                    not re.search(business_words, first["text"], re.I)
                    and re.search(business_words, second["text"], re.I)
                    and len(first["text"].split()) <= 3
                )
                if close_lines and large_letters and brand_then_type:
                    combined_score += 22

                #A short brand directly above a business-type name
                #(e.g. APEX / BRAKE & SUSPENSION) is one company.
                if (close_lines and brand_then_type
                        and len(first["text"].split()) == 1
                        and len(second["text"].split()) <= 4
                        and not re.search(r"\b(?:wof|repair|servicing|automatic|manual|rebuilds)\b",
                                          second["text"], re.I)):
                    combined_score += 22

                candidates.append({
                    "name": combined,
                    "score": combined_score,
                    "y": min(first["y"], second["y"])
                })

    if candidates:
        candidates.sort(
            key=lambda item: (
                -item["score"],
                item["y"],
                -len(item["name"])
            )
        )

        print("\n=== COMPANY DEBUG ===")
        for candidate in candidates[:8]:
            print(candidate["name"], "| SCORE:", candidate["score"])

        best = candidates[0]

        #A customer's name can score a few points simply by being
        #near the top. Don't mistake it for the supplier when OCR
        #cannot read the business logo.
        if best["score"] >= 20:
            return trim_company_name(
                best["name"]
            )

    #Text-only fallback
    top = [
        clean_text(line)
        for line in fallback_lines[:40]
        if clean_text(line)
    ]

    fallback_candidates = []

    for position, line in enumerate(top):
        line = trim_company_name(line)

        if not valid_name_piece(line):
            continue

        score = 0

        if re.search(
            business_words,
            line,
            re.I
        ):
            score += 20

        if re.search(
            r"\b(?:ltd|limited)\b",
            line,
            re.I
        ):
            score += 15

        if position < 12:
            score += 10

        if 2 <= len(line.split()) <= 7:
            score += 5

        if line.isupper():
            score += 2

        fallback_candidates.append(
            (
                score,
                -position,
                line
            )
        )

    if not fallback_candidates:
        return ""

    fallback_candidates.sort(
        reverse=True
    )

    best_score, _, name = (
        fallback_candidates[0]
    )

    return (
        trim_company_name(name)
        if best_score >= 20
        else ""
    )

#CUSTOMER SECTION HELPERS

def find_customer_labels(items):
    pattern = (
        r"\b(?:invoice to|bill to|customer|customer details|"
        r"client|sold to|ship to)\b"
    )
    return [
        item
        for item in items
        if re.search(
            pattern,
            normalize_text(item["text"]),
            re.I
        )
    ]

def is_under_customer_section(
    item,
    customer_labels,
    page_width,
    page_height
):
    for label in customer_labels:
        vertical_distance = item["y"] - label["y"]
        horizontal_distance = abs(
            item["x"]
            - label["x"]
        )
        if (
            vertical_distance > 0
            and vertical_distance < page_height * 0.25
            and horizontal_distance < page_width * 0.35
        ):
            return True
    return False

#BUSINESS ADDRESS

def get_address_from_results(results):
    street_types = (
        r"road|rd|street|st|avenue|ave|drive|dr|lane|ln|place|pl|"
        r"crescent|cres|terrace|tce|highway|hwy|way|parade|"
        r"boulevard|blvd|close|court|ct"
    )
    street_pattern = re.compile(
        rf"\b"
        rf"\d+[A-Za-z]?(?:\s*[-–]\s*\d+[A-Za-z]?)?\s+"
        rf"[A-Za-z0-9 .,'&/\-]+?\s+"
        rf"(?:{street_types})\b",
        re.I
    )
    stop_words = (
        r"phone|mobile|tel|telephone|email|website|www|gst|nzbn|"
        r"invoice|estimate|quote|receipt|customer|bill to|invoice to|"
        r"vehicle|registration|rego|vin|odometer|bank|account"
    )
    candidates = []
    for result in results:
        items = create_items(result)
        if not items:
            continue
        page_width = max(item["right"] for item in items)
        page_height = max(item["bottom"] for item in items)
        median_height = median(item["height"] for item in items)
        customer_labels = find_customer_labels(items)
        site_labels = [
            item for item in items
            if re.search(
                r"\b(?:site address|delivery address|shipping address|"
                r"service address|worksite address|installation address)\b",
                item["text"], re.I
            )
        ]
        ordered_items = sorted(
            items,
            key=lambda item: (
                item["corrected_y"],
                item["x"]
            )
        )
        for index, item in enumerate(ordered_items):
            text = clean_text(item["text"])
            match = street_pattern.search(text)
            if not match:
                continue

            #A site or delivery address belongs to the job/customer,
            #not the business that issued the invoice.
            in_site_section = False
            for label in site_labels:
                vertical_gap = item["corrected_y"] - label["corrected_y"]
                horizontal_gap = abs(item["left"] - label["left"])
                if (0 <= vertical_gap < page_height * 0.12
                        and horizontal_gap < page_width * 0.28):
                    in_site_section = True
                    break
            if in_site_section:
                continue

            if is_under_customer_section(
                item,
                customer_labels,
                page_width,
                page_height
            ):
                continue
            address = clean_text(
                text[match.start():]
            ).strip(" ,")
            #Remove contact details if OCR placed them
            #in the same OCR box.
            address = re.split(
                rf"\b(?:{stop_words})\b",
                address,
                maxsplit=1,
                flags=re.I
            )[0]
            address = clean_text(
                address
            ).strip(" ,")
           
            #Find suburb, city and postcode using OCR coordinates.
            if not re.search(r"\b\d{4}\b", address):

                nearby_lines = []

                for next_item in items:
                    if next_item is item:
                        continue

                    next_text = clean_text(next_item["text"])

                    if not next_text:
                        continue

                    #Must be below the street address.
                    vertical_distance = (
                        next_item["corrected_y"]
                        - item["corrected_y"]
                    )

                    if vertical_distance <= 0:
                        continue

                    #Only search within the nearby address area.
                    if vertical_distance > max(
                        median_height * 3.5,
                        page_height * 0.07
                    ):
                        continue

                    #City/suburb should begin near the street address.
                    left_difference = abs(
                        next_item["left"] - item["left"]
                    )

                    if left_difference > page_width * 0.12:
                        continue

                    #Avoid customer details.
                    if is_under_customer_section(
                        next_item,
                        customer_labels,
                        page_width,
                        page_height
                    ):
                        continue

                    #Avoid labels, contact details and other addresses.
                    if re.search(stop_words, next_text, re.I):
                        continue

                    if street_pattern.search(next_text):
                        continue

                   
                    #Accept NZ place names with spaces, commas,
                    #Māori macrons and optional postcodes.
                    if not re.fullmatch(
                        r"[^\W\d_]+(?:[ ,.'’&/\-]+[^\W\d_]+)*(?:\s+\d{4})?",
                        next_text
                    ):
                        continue


                    nearby_lines.append((
                        vertical_distance,
                        next_text
                    ))

                #Prefer a nearby city line containing a NZ postcode.
                postcode_lines = [
                    line for line in nearby_lines
                    if re.search(r"\b\d{4}\b", line[1])
                ]

                if postcode_lines:
                    postcode_lines.sort(key=lambda line: line[0])
                    address += ", " + postcode_lines[0][1]

                elif nearby_lines:
                    nearby_lines.sort(key=lambda line: line[0])
                    address += ", " + nearby_lines[0][1]

            relative_y = (
                item["y"]
                / page_height
            )
            score = 0
            if relative_y < 0.20:
                score += 30
            elif relative_y < 0.35:
                score += 20
            if item["x"] < page_width * 0.50:
                score += 5
            if re.search(
                r"\b\d{4}\b",
                address
            ):
                score += 10
            if "," in address:
                score += 3
            candidates.append({
                "address": address,
                "score": score,
                "y": item["y"]
            })
    if not candidates:
        return ""
    candidates.sort(
        key=lambda item: (
            -item["score"],
            item["y"]
        )
    )
    return candidates[0]["address"]

#NZ PHONE NUMBERS

def find_phone_matches(text):
    patterns = [
        #+64
        r"\+64[\s()\-]*(?:\d[\s()\-]*){7,10}",

        #NZ mobile / landline
        r"\b0[2-9](?:[\s()\-]*\d){7,9}\b",

        #0800 / 0508
        r"\b0(?:800|508)(?:[\s()\-]*\d){6,8}\b"
    ]

    matches = []

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = clean_text(match.group())
            #Invoice references such as 090926-084 are not phone numbers.
            if re.fullmatch(r"\d{6}-\d{3,5}", value):
                continue
            digits = re.sub(r"\D", "", value)

            if digits.startswith("64"):
                normal = "0" + digits[2:]
            else:
                normal = digits

            if len(normal) < 8:
                continue

            matches.append({
                "value": value,
                "normal": normal
            })

    return matches

def looks_like_nz_phone(text):
    digits = re.sub(r"\D", "", text)
    if digits.startswith("64"):
        digits = "0" + digits[2:]
    if re.fullmatch(
        r"0[2-9]\d{7,9}",
        digits
    ):
        return True
    if re.fullmatch(
        r"0(?:800|508)\d{6,8}",
        digits
    ):
        return True
    return False

def get_phones_from_results(results):
    found = []
    for result in results:
        items = create_items(result)
        if not items:
            continue
        page_width = max(item["right"] for item in items)
        page_height = max(item["bottom"] for item in items)
        customer_labels = find_customer_labels(items)
        phone_labels = [
            item
            for item in items
            if re.search(
                r"\b(?:phone|tel|telephone|mobile|mob)\b",
                normalize_text(item["text"]),
                re.I
            )
        ]
        for item in items:
            phones = find_phone_matches(
                item["text"]
            )
            for phone in phones:
                is_customer_phone = False
                #Tighter customer area for phone numbers.
                for label in customer_labels:
                    vertical_distance = (
                        item["y"]
                        - label["y"]
                    )
                    horizontal_distance = abs(
                        item["x"]
                        - label["x"]
                    )
                    if (
                        vertical_distance > 0
                        and vertical_distance < page_height * 0.20
                        and horizontal_distance < page_width * 0.22
                    ):
                        is_customer_phone = True
                        break
                if is_customer_phone:
                    continue
                if re.search(
                    r"bank|account|a/c|iban|swift",
                    item["text"],
                    re.I
                ):
                    continue
                relative_y = (
                    item["y"]
                    / page_height
                )
                score = 0
                if relative_y < 0.25:
                    score += 20
                elif relative_y < 0.40:
                    score += 10
                for label in phone_labels:
                    vertical_distance = abs(
                        item["y"]
                        - label["y"]
                    )
                    horizontal_distance = abs(
                        item["x"]
                        - label["x"]
                    )
                    if (
                        vertical_distance
                        < max(
                            35,
                            item["height"] * 3
                        )
                        and horizontal_distance
                        < page_width * 0.50
                    ):
                        score += 20
                found.append({
                    "value": phone["value"],
                    "normal": phone["normal"],
                    "score": score,
                    "y": item["y"],
                    "x": item["x"]
                })
    unique = {}
    for item in found:
        normal = item["normal"]
        if (
            normal not in unique
            or item["score"]
            > unique[normal]["score"]
        ):
            unique[normal] = item
    numbers = list(
        unique.values()
    )
    if not numbers:
        return ""
    likely_business = [
        item
        for item in numbers
        if item["score"] >= 10
    ]
    if likely_business:
        numbers = likely_business
    numbers.sort(
        key=lambda item: (
            item["y"],
            item["x"]
        )
    )
    return ", ".join(
        item["value"]
        for item in numbers
    )

#EMAIL

def get_email(text):
    match = re.search(
        r"\b[A-Z0-9._%+\-]+"
        r"@[A-Z0-9.\-]+"
        r"\.[A-Z]{2,}\b",
        text,
        re.I
    )
    return match.group() if match else ""

#GST NUMBER

def get_gst(lines):
    for index, line in enumerate(lines):
        if re.search(
            r"\bnzbn\b",
            line,
            re.I
        ):
            continue
        if not re.search(
            r"\bgst\b",
            line,
            re.I
        ):
            continue
        nearby = " ".join(
            lines[
                max(0, index - 1):
                index + 3
            ]
        )
        #Avoid treating NZBN as GST.
        if re.search(
            r"\bnzbn\b",
            nearby,
            re.I
        ):
            continue
        match = re.search(
            r"\b\d{2,3}[-\s]\d{3}[-\s]\d{3}\b",
            nearby
        )
        if match:
            return re.sub(
                r"\s",
                "-",
                match.group()
            )
        match = re.search(
            r"\b\d{8,9}\b",
            nearby
        )
        if match:
            return match.group()
    return ""

#DATE HELPERS

def looks_like_date(text):
    text = clean_text(text)
    patterns = [
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
        r"\d{4}[/-]\d{1,2}[/-]\d{1,2}"
    ]
    return any(
        re.fullmatch(pattern, text)
        for pattern in patterns
    )




def restore_invoice_prefix(value, number_item, items, page_width, median_height):
    """
    Rejoin an invoice prefix when OCR separates it.

    Example:
        RA-    58214  -> RA-58214

    Only joins prefixes actually detected by OCR.
    """
    value = clean_text(value)

    #Already has letters, so preserve it unchanged.
    if re.search(r"[A-Za-z]", value):
        return value

    if not re.fullmatch(r"\d{3,15}", value):
        return value

    matches = []

    for item in items:
        if item is number_item:
            continue

        prefix = clean_text(item["text"])

        #Explicit alphabetic prefix ending in a hyphen.
        if not re.fullmatch(r"[A-Za-z]{1,6}-", prefix):
            continue

        vertical_gap = abs(
            item["corrected_y"] - number_item["corrected_y"]
        )

        horizontal_gap = number_item["left"] - item["right"]

        if vertical_gap > median_height * 1.5:
            continue

        if not (-median_height <= horizontal_gap <= page_width * 0.08):
            continue

        matches.append((abs(horizontal_gap), prefix))

    if matches:
        matches.sort(key=lambda match: match[0])
        return matches[0][1] + value

    return value



#INVOICE NUMBER
def get_invoice_number_from_results(
    results,
    fallback_lines
):
    label_pattern = re.compile(
        r"\b(?:invoice|estimate|quote|receipt)\s*"
        r"(?:(?:number|no\.?|#)\b|(?=\s*:))|"
        r"\btax\s+invoice\s*:?\s*$",
        re.I
    )

    #Handles formats such as:
    #Tax Invoice N443122576
    #Tax Invoice: INV-2048
    tax_invoice_pattern = re.compile(
    r"\btax\s+invoice\s*"
    r"(?:(?:number|no\.?|#)\s*[:#\-]?\s*|[:#\-]\s*|\s+)"
    r"([A-Z0-9][A-Z0-9_-]{2,30})\b",
    re.I
)

    bad_words = (
        r"phone|mobile|tel|telephone|date|customer|job|order|"
        r"purchase|po|vehicle|rego|registration|vin|odometer|"
        r"gst|nzbn|account|bank"
    )

    #Prefer numbers printed beside a clear invoice label. This also
    #avoids accidentally returning a year like 2026 as the invoice number.
    explicit = re.compile(
        r"\b(?:tax\s+)?(?:invoice|receipt)\s*"
        r"(?:no\.?|number|#|ref(?:erence)?)\s*[:#-]?\s*"
        r"([A-Z0-9][A-Z0-9/_-]{3,35})\b", re.I
    )
    for line in fallback_lines[:60]:
        found = explicit.search(line)
        if found:
            value = found.group(1)
            if (re.search(r"\d", value) and not looks_like_date(value)
                    and not re.fullmatch(r"(?:19|20)\d{2}", value)
                    and (re.search(r"[A-Za-z]", value) or not looks_like_nz_phone(value))):
                return value

    candidates = []

    for result in results:
        items = create_items(result)

        if not items:
            continue

        page_width = max(
            item["right"]
            for item in items
        )

        median_height = median(
            item["height"]
            for item in items
        )

        #Detect standalone invoice numbers.
        page_height = max(item["bottom"] for item in items)

        for item in items:
            value = clean_text(item["text"])

            if item["y"] > page_height * 0.35:
                continue

            if re.fullmatch(
                r"(?:INV|INVOICE)[/-][A-Z0-9]+(?:[/-][A-Z0-9]+)+",
                value,
                re.I
            ):
                return value


        #Tax Invoice N443122576
        for item in items:
            match = tax_invoice_pattern.search(
                item["text"]
            )

            if not match:
                continue

            value = match.group(1)

            if looks_like_date(value):
                continue

            if looks_like_nz_phone(value):
                continue

            if re.search(
                bad_words,
                value,
                re.I
            ):
                continue

            return value

        #Invoice No / Invoice Number / Invoice #
        for label in items:
            match = label_pattern.search(
                label["text"]
            )

            if not match:
                continue

            #Same OCR item:
            #Invoice No: WG-4587
            remaining = clean_text(
                label["text"][match.end():]
            )

            remaining = re.sub(
                r"^[\s:#\-]+",
                "",
                remaining
            )

           
          
            #Accept invoice numbers with spaces around hyphens.
            #Examples: RA- 58214, INV - 2048, WG-4587
            same_item_match = re.search(
                r"\b"
                r"(?=[A-Z0-9_\s-]*\d)"
                r"[A-Z0-9]+"
                r"(?:\s*[-_]\s*[A-Z0-9]+)*"
                r"\b",
                remaining,
                re.I
            )

            if same_item_match:
                value = same_item_match.group()

                #Remove OCR-added spaces around hyphens.
                value = re.sub(r"\s*-\s*", "-", value)
                value = re.sub(r"\s*_\s*", "_", value)

                if (
                    not looks_like_date(value)
                    and not re.fullmatch(r"(?:19|20)\d{2}", value)
                    and not re.fullmatch(r"(?:19|20)\d{2}", value)
                and (re.search(r"[A-Za-z]", value) or not looks_like_nz_phone(value))
                    and not re.search(
                        bad_words,
                        value,
                        re.I
                    )
                ):
                    return value


            #Value beside Invoice No label
            for item in items:
                if item is label:
                    continue

                vertical_distance = abs(
                    item["corrected_y"]
                    - label["corrected_y"]
                )

                if vertical_distance > max(
                    20,
                    median_height * 1.5
                ):
                    continue

                if item["x"] <= label["x"]:
                    continue

                horizontal_distance = (
                    item["x"]
                    - label["x"]
                )

                if (
                    horizontal_distance
                    > page_width * 0.35
                ):
                    continue

                text = clean_text(
                    item["text"]
                )

                if re.search(
                    bad_words,
                    text,
                    re.I
                ):
                    continue

                number_match = re.search(
                    r"\b"
                    r"(?=[A-Z0-9_-]*\d)"
                    r"[A-Z0-9]"
                    r"[A-Z0-9_-]{2,30}"
                    r"\b",
                    text,
                    re.I
                )

                if not number_match:
                    continue

                value = number_match.group()

                if looks_like_date(value):
                    continue

                if looks_like_nz_phone(value):
                    continue
                
                value = restore_invoice_prefix(
                    value,
                    item,
                    items,
                    page_width,
                    median_height
                )
                
                candidates.append({
                    "value": value,
                    "vertical": vertical_distance,
                    "distance": horizontal_distance
                })


    if candidates:
        candidates.sort(
            key=lambda item: (
                item["vertical"],
                item["distance"]
            )
        )

        return candidates[0]["value"]

    #Text fallback:
    #Tax Invoice N443122576
    for line in fallback_lines[:50]:
        match = tax_invoice_pattern.search(
            line
        )

        if match:
            value = match.group(1)

            if (
                not looks_like_date(value)
                and not re.fullmatch(r"(?:19|20)\d{2}", value)
                and (re.search(r"[A-Za-z]", value) or not looks_like_nz_phone(value))
            ):
                return value

    #Text fallback:
    #Invoice No: WG-4587
    for line in fallback_lines[:50]:
        match = re.search(
            r"\b(?:invoice|estimate|quote|receipt)\s*"
            r"(?:(?:number|no\.?|#)\s*[:#\-]?|:)\s*"
            r"([A-Z0-9][A-Z0-9_-]{2,30})",
            line,
            re.I
        )

        if not match:
            continue

        value = match.group(1)

        if looks_like_date(value) or re.fullmatch(r"(?:19|20)\d{2}", value):
            continue

        if re.fullmatch(r"\d+", value) and looks_like_nz_phone(value):
            continue

        return value

    return ""

#INVOICE DATE

def convert_date(value):
    value = clean_text(value)
    value = value.replace(",", "")
    value = value.replace("/", "-")
    value = value.replace(".", "-")
    formats = [
        "%d %B %y",
        "%d %b %y",
        "%B %d %y",
        "%b %d %y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
        #Some receipts use US dates, e.g. 4/23/2021.
        #NZ day-first formats are tried first.
        "%m-%d-%Y",
        "%m-%d-%y"
    ]
    for date_format in formats:
        try:
            parsed = datetime.strptime(
                value,
                date_format
            )
            return parsed.strftime(
                "%Y-%m-%d"
            )
        except ValueError:
            continue
    return ""

def get_date(lines):
    months = (
        r"January|February|March|April|May|June|July|August|September|"
        r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|"
        r"Sep|Sept|Oct|Nov|Dec"
    )

    date_regex = (
        rf"(?:"
        rf"\d{{4}}[/-]\d{{1,2}}[/-]\d{{1,2}}"
        rf"|"
        rf"\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}"
        rf"|"
        rf"\d{{1,2}}\s+(?:{months})\s+\d{{2,4}}"
        rf"|"
        rf"(?:{months})\s+\d{{1,2}},?\s+\d{{2,4}}"
        rf")"
    )

    preferred = []
    fallback = []

    for index, line in enumerate(lines):
        if re.search(
            r"valid until|expiry|due date|payment due",
            line,
            re.I
        ):
            continue

        matches = re.findall(
            date_regex,
            line,
            re.I
        )

        if not matches:
            continue

        if re.search(
            r"invoice date|estimate date|quote date|"
            r"date issued|issue date|^date\b",
            line,
            re.I
        ):
            preferred.extend(matches)

        elif index < 30:
            fallback.extend(matches)

    for value in preferred + fallback:
        converted = convert_date(value)

        if converted:
            return converted

    return ""

#MONEY / TOTAL

def extract_money(text):
    text = clean_text(text)
    matches = re.findall(
        r"(?<![\d.])(?:NZD\s*|NZ\$\s*|\$\s*)?"
        r"(\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})(?!\d)",
        text,
        re.I
    )
    return [value.replace(",", "") for value in matches]

def get_total(lines, table=None):
    preferred_labels = [
        r"grand total",
        r"invoice total",
        r"estimate total",
        r"quote total",
        r"amount payable",
        r"total payable",
        r"total due",
        r"total amount",
        r"total incl\.?\s*gst",
        r"amount incl\.?\s*gst",
        r"invoice amount",
        r"total"
    ]
    ignored_labels = (
        r"subtotal|sub total|sub-total|"
        r"balance due|amount due|paid|payment|"
        r"labour total|labor total|parts total|"
        r"tyres total|tires total"
    )
    for label in preferred_labels:
        for index in range(len(lines) - 1, -1, -1):
            
            line = clean_text(
            lines[index]
        )

        #Ignore invoice table header rows such as:
        #DESCRIPTION QTY UNIT PRICE GST TOTAL
        if (
             re.search(r"\btotal\b", line, re.I)
             and re.search(
                 r"\b(?:description|qty|quantity|unit price|rate)\b",
                line,
                re.I
            )
        ):
            continue

        if re.search(
            ignored_labels,
            line,
            re.I
        ):
            continue
        for label in preferred_labels:
            match = re.search(
                r"\b"
                + label
                + r"\b",
                line,
                re.I
            )
            if not match:
                continue
            money = extract_money(
                line[match.end():]
            )
            if money:
                return money[-1]
            for next_line in lines[
                index + 1:
                index + 3
            ]:
                if re.search(
                    r"paid|balance due|amount due|payment",
                    next_line,
                    re.I
                ):
                    break
                money = extract_money(
                    next_line
                )
                if money:
                    return money[-1]
    #Paid invoices may show only Subtotal, GST, Payment and Balance.
    #Read each amount beside its own label, not a payment or balance.
    subtotal = None
    gst_options = []
    payment_options = []

    labels = re.compile(
        r"\b(?:sub[\s-]*total|gst|less\s+payments?|payments?|"
        r"balance(?:\s+due)?|grand\s+total|invoice\s+total)\b",
        re.I
    )

    for line in lines:
        found_labels = list(labels.finditer(line))
        for index, label in enumerate(found_labels):
            next_start = (found_labels[index + 1].start()
                          if index + 1 < len(found_labels) else len(line))
            value_text = line[label.end():next_start]
            amounts = extract_money(value_text)
            if not amounts:
                continue

            value = float(amounts[0])
            label_text = label.group().lower()

            if re.fullmatch(r"sub[\s-]*total", label_text):
                subtotal = value
            elif label_text == "gst":
                #Ignore GST registration numbers and contact details.
                if not re.match(r"\s*(?:no\.?|number|registration)",
                                value_text, re.I):
                    gst_options.append(value)
            elif "payment" in label_text:
                payment_options.append(value)

    if subtotal is not None:
        #GST is a tax component, never greater than the subtotal.
        #Accept 0%-25% to allow common invoice tax/rounding layouts.
        valid_gst = [value for value in gst_options
                     if 0 <= value <= subtotal * 0.25]
        if valid_gst:
            #Prefer a GST amount near the standard NZ GST rate.
            gst = min(valid_gst, key=lambda value: abs(value - subtotal * 0.15))
            return f"{subtotal + gst:.2f}"

        #If GST wasn't readable, a fully paid invoice may give us
        #the total in 'Less Payment'. Only use it when it agrees
        #with a 15% GST calculation, not for arbitrary payments.
        expected_total = round(subtotal * 1.15, 2)
        for payment in payment_options:
            if abs(payment - expected_total) <= 0.02:
                return f"{payment:.2f}"

    #Some invoices have a printed final figure with no readable
    #"Total" label. Only accept it when it agrees with every
    #clearly recognised line item. Never invent a calculated total.
    if table and len(table.get("rows", [])) >= 2:
        values = []
        for row in table["rows"]:
            value = clean_text(row.get("total", ""))
            if not re.fullmatch(r"-?\$?\s*\d[\d,]*\.\d{2}", value):
                values = []
                break
            values.append(float(value.replace("$", "").replace(",", "")))

        if values:
            expected = round(sum(values), 2)
            if expected > 0:
                #Prefer the end of the document, after the items.
                for line in reversed(lines[len(lines) // 2:]):
                    for amount in extract_money(line):
                        if abs(float(amount) - expected) <= 0.02:
                            return f"{expected:.2f}"

    return ""

#INVOICE ODOMETER

def get_invoice_odometer(lines):
    #Look for mileage labels on the invoice
    pattern = r"\b(?:odometer|odo|mileage|kilometres|kilometers|kms|mi)\b"

    for index, line in enumerate(lines):
        match = re.search(pattern, line, re.I)

        if not match:
            continue

        #Read the number after the label
        value_text = line[match.end():]
        number = re.search(r"\b\d{1,3}(?:[,\s]\d{3})+|\b\d{4,7}\b", value_text)

        #Sometimes the reading appears on the next line
        if not number and index + 1 < len(lines):
            value_text = lines[index + 1]
            number = re.search(r"\b\d{1,3}(?:[,\s]\d{3})+|\b\d{4,7}\b", value_text)

        if number:
            reading = int(re.sub(r"[,\s]", "", number.group()))

            if 0 <= reading <= 2500000:
                return reading

    return None
#Make one short description for work counted as Service.
def get_service_description(table, notes, category):
    if "Service" not in category.split("/"):
        return ""

    rows = [str(row.get("description", "")) for row in table.get("rows", [])]
    items = " ".join(rows).lower()
    notes = str(notes or "")

    #Only completed work should appear in the service description.
    completed_notes = []
    for line in notes.splitlines():
        if re.search(r"\b(recommend\w*|advis\w*|next service|due|monitor|future|quote|estimate|should|needs to|not carried out|not done|not performed|no (?:engine )?oil service|no oil change)\b", line, re.I):
            continue
        completed_notes.append(line)
    #Remove inspection-only findings before looking for completed work.
    completed_notes = [line for line in completed_notes if not re.search(
        r"\b(?:inspected?|checked?|check|approximately|remaining|recommendations?|findings)\b.{0,50}\b(?:pads?|rotors?|discs?|tyres?|tires?)\b|"
        r"\b(?:pads?|rotors?|discs?)\b.{0,35}\b(?:remaining|inspected?|checked?)\b", line, re.I)]
    done = " ".join(completed_notes).lower()
    text = items + " " + done
    description = []

    def has(pattern, value=text):
        return re.search(pattern, value, re.I) is not None

    #Only call brake work a repair when a real fault is described.
    #"No brake vibration" is a normal test result, not a fault.
    brake_repair = False
    for line in rows + completed_notes:
        line = line.lower()
        if re.search(r"\b(?:no|without|not|free of)\s+(?:further\s+)?(?:brake\s+)?(?:vibration|shudder)\b", line):
            line = re.sub(r"\b(?:no|without|not|free of)\s+(?:further\s+)?(?:brake\s+)?(?:vibration|shudder)\b", "", line)
        if re.search(r"\b(?:brake|disc|rotor)\b.{0,65}\b(?:vibration|shudder|fault|failure|machin(?:e|ed|ing)|skim|correct)\b|\b(?:vibration|shudder)\b.{0,60}\b(?:brake|disc|rotor)\b", line):
            brake_repair = True
            break

    #For parts-only rows, use neutral descriptions unless work is confirmed.
    def worked(part):
        return has(r"\b(?:replace[ds]?|replacing|change[ds]?|changing|fitt?ed|fit|install(?:ed)?|renew(?:ed)?)\b.{0,65}" + part, done) or has(part + r".{0,40}\b(?:replace[ds]?|changed|fitt?ed|installed|renewed)\b", done) or any(has(r"\b(?:replace[ds]?|changing|change[ds]?|fit|fitted|install(?:ed)?)\b.{0,45}" + part, row.lower()) or has(part + r".{0,35}\b(?:replace[ds]?|changed|fitt?ed|installed)\b", row.lower()) for row in rows)

    oil = has(r"\b(?:engine oil|oil change|oil service|\d{1,2}w[ -]?\d{2}\s+service package|havoline\s+\d{1,2}w[ -]?\d{2})\b")
    oil_filter = has(r"\boil filter\b|\boil\s+(?:and|&)\s+filter\b")
    if oil and oil_filter:
        description.append("Engine oil and filter change" if worked(r"\b(?:engine oil|oil filter|oil)\b") or has(r"\b(?:oil service|oil & filter change|oil and filter service|oil and filter replacement|full service)\b") else "Engine oil and oil filter")
    elif oil:
        description.append("Engine oil change" if worked(r"\b(?:engine oil|oil)\b") else "Engine oil")
    elif oil_filter:
        description.append("Oil filter replacement" if worked(r"\boil filter\b") else "Oil filter")

    for pattern, name, replacement in [
        (r"\bair filter\b", "Air filter", "Air filter replacement"),
        (r"\bcabin (?:air )?filter\b", "Cabin filter", "Cabin filter replacement"),
        (r"\bfuel filter\b", "Fuel filter", "Fuel filter replacement"),
        (r"\bspark plugs?\b", "Spark plugs", "Spark plug replacement"),
    ]:
        if has(pattern):
            description.append(replacement if worked(pattern) else name)

    #A plain inspection does not mean pads/discs have been replaced.
    inspected_only = has(r"\b(?:inspect(?:ed|ion)?|check(?:ed)?)\b.{0,35}\b(?:brake\s+)?(?:pads?|discs?|rotors?)\b", done)
    #Brake repairs (e.g. machining discs to fix shudder) are not routine service.
    if not brake_repair:
        pads = has(r"\bbrake pads?\b|\b(?:front|rear) pads\b|\b(?:disc|disk) pads?\b", items) or has(r"\bbrake pads?\b|\b(?:front|rear) pads\b|\b(?:disc|disk) pads?\b", done)
        rotors = has(r"\bbrake rotors?\b|\b(?:front|rear) rotors\b|\b(?:front|rear) brake discs?\b|\bbrake discs?\b", items) or has(r"\bbrake rotors?\b|\b(?:front|rear) rotors\b|\b(?:front|rear) brake discs?\b|\bbrake discs?\b", done)
        brake_done = (worked(r"\b(?:brake pads?|brake rotors?|(?:front|rear) (?:pads|rotors))\b")
                      or has(r"\b(?:new|renew)\s+(?:disc\s+)?pads\b|\b(?:disc\s+)?pads\s+(?:replaced|fitted)\b", done)
                      or has(r"\b(?:front|rear) brakes replaced\b", done)
                      or has(r"\b(?:front|rear\s+)?pads\s+and\s+rotors\s+(?:replaced|changed|fitted)\b", done)
                      or has(r"\b(?:front|rear\s+)?(?:brake\s+)?pads\s+and\s+(?:brake\s+)?rotors\s+(?:replaced|changed|fitted)\b", done)
                      or has(r"\b(?:brake\s+)?discs?\s+and\s+pads\s+(?:replaced|changed|fitted)\b", done)
                      or has(r"\breplace\s+(?:front\s+)?brake\s+discs?\s+and\s+pads\b", items))
        if inspected_only and not brake_done and not any(re.search(r"\b(?:new|renew|replaced|replace|fitted)\b.{0,35}\b(?:disc\s+)?pads?\b", row, re.I) for row in rows):
            pads = False
            rotors = False
        if pads and rotors:
            #Machined discs are not new discs.
            disc_machining = has(r"\b(?:machine[ds]?|machining|skim(?:med|ming)?)\s+(?:all\s+)?(?:brake\s+)?(?:discs?|rotors?)\b", text)
            disc_replaced = has(r"\b(?:replace[ds]?|new|renewed|fitted)\b.{0,35}\b(?:brake\s+)?(?:discs?|rotors?)\b(?!\s+pads?)|\b(?:brake\s+)?(?:discs?|rotors?)\s+(?:replaced|fitted|renewed)\b", text)
            if disc_machining and not disc_replaced:
                description.append("Brake pad replacement" if brake_done else "Brake pads")
                description.append("Brake disc machining")
            else:
                description.append("Brake pads and rotors replaced" if brake_done else "Brake pads and rotors")
        elif pads:
            description.append("Brake pad replacement" if brake_done else "Brake pads")
        elif rotors:
            description.append("Brake rotor replacement" if brake_done else "Brake rotors")

    #OCR may miss an entire brake invoice table; completed-work notes
    #still describe the pad replacement clearly.
    if not any("brake pad" in entry.lower() for entry in description):
        if re.search(r"\b(?:front|rear|brake)\s+(?:and\s+(?:rear|front)\s+)?brake\s+pads?\s+replaced\b|\bfront and rear brake pads replaced\b", done):
            description.append("Brake pad replacement")

    if has(r"\bbrake fluid\b|\bbrake bleed\b"):
        description.append("Brake fluid service" if has(r"\b(?:flush(?:ed)?|bleed|bled|changed|change|replace[ds]?)\b.{0,35}\bbrake fluid\b|\bbrake fluid\b.{0,25}\b(?:flush|change|replace)\b|\b(?:top up and bleed|bleed (?:the )?system)\b", text) else "Brake fluid")

    if has(r"\b(?:coolant|radiator flush)\b"):
        #A coolant top-up is not a coolant flush/replacement.
        coolant_topped_only = has(r"\bcoolant\b.{0,30}\b(?:top[ -]?up|topped up)\b|\b(?:top[ -]?up|topped up)\b.{0,30}\bcoolant\b") and not has(r"\bcoolant\b.{0,30}\b(?:flush(?:ed)?|replac(?:ed|ement)|changed)\b|\b(?:flush(?:ed)?|replaced|changed)\b.{0,30}\bcoolant\b")
        if not coolant_topped_only and has(r"\bcoolant\b.{0,25}\b(?:flush(?:ed)?|changed|change|replaced|replacement)\b|\b(?:flush(?:ed)?|changed|change|replaced)\b.{0,25}\bcoolant\b|\bradiator flush\b", text):
            description.append("Coolant change")
        elif has(r"\b(?:top[ -]?up|topped up)\b.{0,25}\bcoolant\b|\bcoolant\b.{0,25}\b(?:top[ -]?up|topped up)\b", text):
            description.append("Coolant top-up")
        elif has(r"\bcoolant additive\b|\bwater wetter\b") and not has(r"\blong life coolant\b"):
            description.append("Coolant additive")
        else:
            description.append("Coolant")

    for pattern, name in [
        (r"\b(?:transmission fluid|gearbox oil)\b", "Transmission fluid"),
        (r"\b(?:diff oil|differential oil)\b", "Differential oil"),
    ]:
        if has(pattern):
            description.append(name + " change" if worked(pattern) else name)

    #Include a few common OCR mistakes found in printed tyre receipts.
    tyre_fit = has(r"\b(?:fit|fitted|fitting|replace|replaced|new)\s*(?:tyre|tire|ttre|tyres|tires)s?\b|\btyre (?:fit|fitting|replacement)\b")
    #A tyre pressure check is not tyre fitting or replacement.
    tyre_item = any(re.search(r"\b(?:tyres?|tires?|ttre)\b|\b\d{3}/\d{2}r\d{2}\b", row, re.I) for row in rows)
    if tyre_fit:
        description.append("Tyre fitting")
    elif tyre_item and not has(r"\b(?:tyre condition|tyre pressure|tyre pressures|tyre stewardship)\b", items):
        description.append("Tyres")
    if has(r"\b(?:wheel|wiheel|wheal|tyre|tire)\s+balanc\w*\b|\bfit (?:and|&) balance\b"):
        description.append("Wheel balancing")
    if has(r"\b(?:wheel|wheal|wiheel)\s+ali(?:gn|gm|gm)m?\w*\b") and not has(r"\brecommend\b.{0,25}\bwheel alignment\b", notes.lower()):
        description.append("Wheel alignment")
    if has(r"\b(?:wof|warrant of fitness)\b"):
        description.append("WOF inspection")
    if has(r"\b(?:wiper blades?|windscreen wipers?)\b"):
        description.append("Wiper blade replacement" if worked(r"\b(?:wiper blades?|windscreen wipers?)\b") else "Wiper blades")
    if has(r"\btiming belt\b"):
        description.append("Timing belt replacement" if worked(r"\btiming belt\b") else "Timing belt")

    if not description and has(r"\b(?:full service|general service|scheduled service|routine service)\b"):
        description.append("General vehicle service")

    return ", ".join(description)

#Only count clearly completed engine oil or general servicing.
def counts_as_general_service(table, notes):
    rows = [str(row.get("description", "")) for row in table.get("rows", [])]
    completed = []

    for line in rows + str(notes or "").splitlines():
        line = clean_text(line).lower()
        if not line:
            continue

        #Skip recommendations and explicitly unperformed jobs.
        if re.search(r"\b(recommend\w*|advis\w*|next service|due|upcoming|future|quote|estimate|declined|not carried out|not done|not performed|no oil service|no engine oil service|no oil change|needs to|should)\b", line):
            continue

        #These fluid jobs are not general engine oil services.
        if re.search(r"\b(transmission|gearbox|differential|diff|brake|power steering)\b", line) and not re.search(r"\bengine oil\b", line):
            continue

        completed.append(line)

    text = " ".join(completed)

    if re.search(r"\b(?:full|major|general|minor|logbook|scheduled|routine|basic|annual|regular)\s+(?:vehicle\s+|car\s+)?servic(?:e|ing)\b", text):
        return True
    if re.search(r"\b\d{1,3}[, ]?\d{3}\s*(?:km|kilometres?)\s+service\b", text):
        return True
    if re.search(r"\b(?:engine\s+)?oil\s+(?:and|&)\s+(?:oil\s+)?filter\s+(?:change|chang(?:ed|ing)|replac(?:e|ed|ement)|service)\b", text):
        return True
    if re.search(r"\b(?:engine\s+)?oil\s+(?:change|changed|changing|service|servicing)\b", text):
        return True
    if re.search(r"\b(?:chang(?:e|ed|ing)|replac(?:e|ed|ing))\s+(?:the\s+)?engine\s+oil\b", text):
        return True

    #Recognise separate engine oil and oil filter invoice rows.
    has_engine_oil = bool(re.search(r"\bengine\s+oil\b|\b(?:0|5|10|15|20)w[- ]?(?:20|30|40|50|60)\b", text))
    has_oil_filter = bool(re.search(r"\boil\s+filter\b", text))
    return has_engine_oil and has_oil_filter

#CATEGORY

def get_category(text):
    strong_service_patterns = [
        #General servicing
        r"\bservice\b",
        r"\bservicing\b",
        r"\bmaintenance\b",
        r"\boil change\b",
        r"\bengine oil\b",
        r"\boil filter\b",
        r"\bair filter\b",
        r"\bcabin filter\b",
        r"\bfuel filter\b",
        r"\bspark plug\b",
        r"\bspark plugs\b",

        #Fluids
        r"\bbrake fluid\b",
        r"\bcoolant\b",
        r"\btransmission fluid\b",
        r"\bgearbox oil\b",
        r"\bdiff oil\b",
        r"\bdifferential oil\b",
        r"\bfluid change\b",

        #Brakes
        r"\bbrake pad\b",
        r"\bbrake pads\b",
        r"\bbrake rotor\b",
        r"\bbrake rotors\b",
        r"\bdisc rotor\b",
        r"\bdisc rotors\b",
        r"\bbrake cleaner\b",
        r"\bbrake service\b",

        #Tyres / wheels
        r"\btyre\b",
        r"\btyres\b",
        r"\btire\b",
        r"\btires\b",
        r"\bwheel balancing\b",
        r"\bwheel balance\b",
        r"\bfit and balance\b",
        r"\bfit & balance\b",
        r"\btyre rotation\b",
        r"\btire rotation\b",
        r"\bpuncture repair\b",
        r"\bvalve replacement\b",
        r"\btyre valve\b",

        #WOF / inspection
        r"\bwof\b",
        r"\bwarrant of fitness\b",
        r"\binspection\b",
        r"\bvehicle inspection\b",

        #Other normal maintenance
        r"\bbattery replacement\b",
        r"\breplace battery\b",
        r"\bwiper blade\b",
        r"\bwiper blades\b",
        r"\btiming belt replacement\b",

        #Scheduled servicing
        r"\b\d{1,3}[,\s]?\d{3}\s*km service\b",
        r"\b\d{1,3}[,\s]?\d{3}\s*kilometre service\b"
    ]

    weak_service_patterns = [
        r"\bwheel alignment\b",
        r"\balignment\b"
    ]

    repair_patterns = [
        #Faults / failures
        r"\b(?:diagnose|diagnosed|rectify|fix|repair|found|traced)\s+(?:an?\s+)?fault\b",
        r"\bfault\s+(?:found|identified|detected)\b",
        r"\bfaulty\b",
        r"\bfailed\b",
        r"\bfailure\b",
        r"\bbroken\b",
        r"\bdamaged\b",
        r"\bleak\b",
        r"\bleaking\b",
        r"\brepair labour\b",
        r"\brepair work\b",

        #Electrical / sensor
        r"\babs sensor\b",
        r"\bwheel speed sensor\b",
        r"\bspeed sensor\b",
        r"\bignition coil\b",
        r"\balternator\b",
        r"\bstarter motor\b",
        r"\bstarter\b",

        #Cooling system
        r"\bradiator\b",
        r"\bwater pump\b",
        r"\bthermostat\b",
        r"\bcoolant hose\b",

        #Drivetrain / mechanical
        r"\bclutch\b",
        r"\bgearbox repair\b",
        r"\btransmission repair\b",
        r"\bengine repair\b",
        r"\bhead gasket\b",
        r"\bcv joint\b",
        r"\bdrive shaft\b",
        r"\bdriveshaft\b",
        r"\bwheel bearing\b",

        #Suspension / steering repairs
        r"\bball joint\b",
        r"\btie rod\b",
        r"\btie rod end\b",
        r"\bcontrol arm\b",
        r"\bsuspension bush\b",
        r"\bsteering rack\b",

       #Other repair wording
        r"\bdiagnose fault\b",
        r"\bdiagnosed fault\b",
        r"\brectify fault\b",
        r"\bfix fault\b",
        r"\bboost hose\b",

        #Broken glass and replacement windows are repairs.
        r"\b(?:windscreen|windshield|window|glass)\s+(?:replacement|repair)\b",
        r"\b(?:replace|replaced|repair|repaired|fit|fitted|supply and fit)\b[^\n]{0,55}\b(?:window|windscreen|windshield|glass)\b",
        r"\b(?:rear|front|side)\s+(?:window|windscreen|windshield|glass)\b",

        #Shocks replaced because they were worn are repair work.
        r"\b(?:shock absorbers?|shocks?)\b[^\n]{0,50}\b(?:replaced|replacement)\s+due to wear\b",
        r"\b(?:replaced|replace)\b[^\n]{0,30}\b(?:shock absorbers?|shocks?)\b[^\n]{0,30}\bdue to wear\b",

        #Worn or damaged suspension components.
        r"\b(?:differential|diff|suspension)\s+(?:mount|mounting|bush|bushes)\b",
        r"\b(?:bush|bushes|mount|mounts)\s+(?:replacement|repair)\b",

        #Brakes are repairs when addressing a fault, not routine pad changes.
        r"\bbrake\s+(?:shudder|vibration|fault|failure|problem|repair)\b",
        r"\b(?:machine|machined|skimm?ed)\b[^\n]{0,35}\b(?:brake|disc|rotor)\b",
    ]

    modification_patterns = [

        #Auxiliary lighting modifications
        r"\b(?:led\s+)?driving lights?\s+kit\b",
        r"\b(?:fit|install|fitting|installation)\s+(?:led\s+)?driving lights?\b",
        r"\b(?:light bar|lightbar)\s+(?:kit|installation|install)\b",

        r"\bmodification\b",
        r"\b(?:oil\s+)?catch can\b",
        r"\bmodifications\b",
        r"\bmodified\b",
        r"\bupgrade\b",
        r"\bupgraded\b",

        #Suspension modifications
        r"\bcoilover\b",
        r"\bcoilovers\b",
        r"\blowering spring\b",
        r"\blowering springs\b",

        #Engine / tuning modifications
        r"\becu tune\b",
        r"\bdyno tune\b",
        r"\bperformance tune\b",
        r"\becu tuning\b",
        r"\bengine tuning\b",
        r"\bremap\b",
        r"\bboost controller\b",
        r"\b(?:aftermarket|performance|upgrade|upgraded)\s+intercooler\b",
        r"\bintercooler\s+(?:upgrade|kit|install|installation)\b",
        r"\bcold air intake\b",
        r"\bperformance intake\b",

        #Exhaust modifications
        r"\bexhaust upgrade\b",
        r"\baftermarket exhaust\b",
        r"\bcatback\b",
        r"\bcat back\b",

        #Exterior modifications
        r"\bbody kit\b",
        r"\bspoiler\b",
        r"\bsplitter\b",
        r"\bdiffuser\b"
    ]

    strong_service_score = sum(
        len(re.findall(pattern, text, re.I))
        for pattern in strong_service_patterns
    )

    weak_service_score = sum(
        len(re.findall(pattern, text, re.I))
        for pattern in weak_service_patterns
    )

    #A clear 'no fault found' statement is not repair work.
    repair_text = re.sub(
        r"\b(?:no|without)\s+(?:further\s+)?(?:faults?|fault codes?|issues?|problems?|leaks?)\s+(?:found|present|detected)?\b",
        "", text, flags=re.I
    )

    repair_score = sum(
        len(re.findall(pattern, repair_text, re.I))
        for pattern in repair_patterns
    )

    modification_score = sum(
        len(re.findall(pattern, text, re.I))
        for pattern in modification_patterns
    )

    matched_categories = []

    #Strong service items always count as Service.
    if strong_service_score > 0:
        matched_categories.append("Service")

    #Weak service evidence such as wheel alignment only
    #counts when there is no repair or modification.
    elif (
        weak_service_score > 0
        and repair_score == 0
        and modification_score == 0
    ):
        matched_categories.append("Service")

    #Repair evidence.
    if repair_score > 0:
        matched_categories.append("Repair")

    #Modification evidence.
    if modification_score > 0:
        matched_categories.append("Modification")

    #Default if nothing useful was found.
    if not matched_categories:
        return "Service"

    return "/".join(matched_categories)

def get_category_text_from_table(table, fallback_text=""):
    descriptions = []

    for row in table.get("rows", []):
        description = clean_text(
            row.get(
                "description",
                ""
            )
        )

        if description:
            descriptions.append(
                description
            )

    if descriptions:
        return "\n".join(descriptions)

    return fallback_text

#INVOICE NOTES
def looks_like_vin(text):
    value = re.sub(
        r"[^A-Za-z0-9]",
        "",
        text
    ).upper()

    #VINs are normally 17 characters, but OCR can
    #occasionally add or drop a character.
    if len(value) < 16 or len(value) > 18:
        return False

    #Must contain both letters and numbers.
    if not re.search(r"[A-Z]", value):
        return False

    if not re.search(r"\d", value):
        return False

    #VIN-like strings are compact alphanumeric values.
    return bool(
        re.fullmatch(
            r"[A-Z0-9]{16,18}",
            value
        )
    )

def classify_note_line(text):
    #Separate mechanic notes from payment details and customer messages.
    #The same checks work for any supplier or invoice layout.
    text = clean_text(text)
    if not text:
        return "skip"

    #Once a note panel reaches its footer, don't read the rest of it.
    if re.search(
        r"^(?:thank(?:\s+you|s)(?:\b|!)|drive safe\b|"
        r"please (?:use your invoice|make payment|refer to)|"
        r"if you have any questions|"
        r"all parts and labo(?:u)?r are warranted|"
        r"(?:warranty|warranties)\s*[:]|"
        r"bank details(?: for payment)?\b|"
        r"payment (?:details|information|instructions)\b|"
        r"received by(?:\s*\(customer\))?\s*:?)|"
        r"^(?:pay by|cash or eftpos|eftpos or cash|"
        r"goods remain (?:the )?property|"
        r"ref\s*:|payment reference\s*:|"
        r"all work completed to (?:nz|new zealand) (?:industry )?standards)",
        text, re.I
    ):
        return "end"

    #A heading or value from a neighbouring invoice/payment column.
    if re.search(
        r"^(?:sub[ -]?total(?:\s*\(.*?\))?|gst(?:\s*\d+%)?(?:\s*\(.*?\))?|"
        r"(?:grand\s+)?total(?:\s+amount)?(?:\s*\(.*?\))?|"
        r"amount (?:paid|due)|balance(?:\s+due)?|"
        r"bank(?:\s+account)?|account(?:\s+(?:name|no|number))?|"
        r"reference|payment(?:\s+(?:method|terms|status))?|"
        r"eftpos|authori[sz]ed|signature|"
        r"(?:technician|customer)\s*/\s*authori[sz]ed\s+signature|"
        r"invoice(?:\s+(?:no|number|date))?|"
        r"terms and conditions|"
        r"deposit received|outstanding balance)\s*[:\-]?\s*(?:$|\S)",
        text, re.I
    ):
        return "skip"

    #Don't mix neighbouring table labels, dates or quantities into notes.
    if re.fullmatch(r"(?:description|item|quantity|qty(?:/hours)?|rate|unit price|amount|total)", text, re.I):
        return "skip"
    if re.fullmatch(r"\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+20\d{2}", text, re.I):
        return "skip"
    if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:hrs?|hours?)", text, re.I):
        return "skip"

    #Skip unreadable fragments, but preserve genuine short service notes.
    if not re.search(r"[A-Za-z]{2,}", text):
        return "skip"
    if len(text) < 7:
        return "skip"
    return "keep"


def is_signature_name(line, items, page_height, text_height):
    #A handwritten name immediately above a signature label is not a
    #mechanic note. Use its location rather than any particular name.
    line = clean_text(line)
    if not re.fullmatch(r"[A-Za-zÀ-ÿ]+(?:[ '\-]+[A-Za-zÀ-ÿ]+){1,3}", line):
        return False

    signatures = [
        item for item in items
        if re.search(r"\b(?:authori[sz]ed|technician|customer)\b.*\bsignature\b|"
                     r"\bsignature\b", item["text"], re.I)
    ]
    if not signatures:
        return False

    names = [item for item in items if clean_text(item["text"]) == line]
    for name in names:
        if name["corrected_y"] < page_height * 0.65:
            continue
        for signature in signatures:
            gap = signature["corrected_y"] - name["corrected_y"]
            if (0 <= gap <= text_height * 5
                    and abs(name["left"] - signature["left"]) <
                    max(name["width"], signature["width"]) * 2):
                return True
    return False


def get_notes_from_results(results, table_rows=None):
    note_sections = []
    table_rows = table_rows or []
    description_texts = {
        normalize_text(row.get("description", ""))
        for row in table_rows if row.get("description")
    }

    for result in results:
        items = create_items(result)
        if not items:
            continue
        page_height = max(item["bottom"] for item in items)
        text_height = median(item["height"] for item in items)

        #Named service sections take priority over guessing note paragraphs.
        sections = extract_invoice_sections(result)
        service_sections = {
            "work completed": "Work Completed",
            "work carried out": "Work Completed",
            "work completed notes": "Work Completed",
            "work carried out notes": "Work Completed",
            "notes work completed": "Work Completed",
            "notes work carried out": "Work Completed",
            "notes and work carried out": "Work Completed",
            "workshop notes work completed": "Work Completed",
            "notes and work completed": "Work Completed",
            "inspection findings": "Inspection Findings",
            "recommendations": "Recommendations",
            "notes": "Notes",
            "mechanic notes": "Notes",
            "mechanics notes": "Notes",
            "technician notes": "Notes",
            "mechanic service notes": "Notes",
            "workshop notes": "Notes",
            "service notes": "Notes",
            "repair notes": "Notes",
            "job notes": "Notes",
            "comments": "Notes",
            "remarks": "Notes"
        }

        matched_sections = 0
        for section in sections:
            name = normalize_text(section["heading"])
            if name not in service_sections:
                continue

            cleaned_lines = []
            for line in section.get("content", "").splitlines():
                line = clean_text(line)
                kind = classify_note_line(line)
                if is_signature_name(line, items, page_height, text_height):
                    continue

                #Stop after a customer footer or warranty paragraph.
                #Financial labels beside another section are just skipped.
                if kind == "end":
                    break
                if kind == "skip":
                    continue

                #Don't repeat the invoice's priced rows as service notes.
                if name in {"work carried out", "work completed"} and \
                        normalize_text(line.strip("-• ")) in description_texts:
                    continue
                cleaned_lines.append(line)

            if cleaned_lines:
                heading = service_sections[name]
                paragraph = heading + ":\n" + "\n".join(cleaned_lines)
                if paragraph not in note_sections:
                    note_sections.append(paragraph)
                matched_sections += 1

        if matched_sections:
            continue

        items.sort(key=lambda item: (item["corrected_y"], item["left"]))
        page_width = max(item["right"] for item in items)
        page_height = max(item["bottom"] for item in items)
        text_height = median(item["height"] for item in items)

        #Find an invoice's item table and total section.
        headers = find_table_columns(items)
        table_y = max((h["y"] for h in headers.values()), default=0)

        total_labels = [
            item for item in items
            if item["corrected_y"] > table_y
            and re.match(r"^(?:sub\s*total|grand total|invoice total|total)(?:\s+nzd)?$",
                         normalize_text(item["text"]), re.I)
        ]
        first_total_y = min((item["corrected_y"] for item in total_labels), default=None)

        #Payment instructions mark the end of the useful invoice content.
        footers = [
            item["corrected_y"] for item in items
            if re.search(r"please make payment|bank details|terms and conditions|"
                         r"payment due on|payment instructions|"
                         r"authorised and received by", item["text"], re.I)
        ]
        footer_y = min(footers) if footers else page_height + text_height

        #Keep notes that are written on the same line as their heading.
        for item in items:
            match = re.match(
                r"^(?:notes?|mechanic notes?|service notes?|comments?|remarks?|"
                r"work completed|work carried out)\s*:\s*(.+)$",
                clean_text(item["text"]), re.I
            )
            if match and match.group(1) not in note_sections:
                #Do not keep customer thank-you text as workshop notes.
                if classify_note_line(match.group(1)) == "keep":
                    note_sections.append(match.group(1))

        #Labelled notes also work when the document has no item table.
        headings = [
            item for item in items
            if re.fullmatch(
                r"(?:mechanic(?:'s)? notes?|technician(?:'s)? notes?|"
                r"workshop notes?|service notes?|repair notes?|"
                r"job notes?|notes?|comments?|remarks?|"
                r"notes?\s*[/&]\s*work (?:completed|carried out)|"
                r"work completed|work carried out|service comments?|"
                r"recommendations?|messages?|sale notes)\s*:?,?",
                clean_text(item["text"]), re.I
            )
        ]

        #Where totals are to the right, notes often occupy the left side.
        total_x = min((item["left"] for item in total_labels), default=page_width)
        amount_x = headers.get("total", {}).get("x", page_width)
        notes_right = min(page_width * 0.78, max(total_x, amount_x * 0.70))

        def is_paragraph_line(item):
            text = clean_text(item["text"])
            if not text or looks_like_vin(text):
                return False

            if classify_note_line(text) != "keep":
                return False

            #Ignore signatures and authorisation labels.
            if re.search(
                r"authorised|authorized|received by|"
                r"signed by|signature|approved by",
                text,
                re.I
            ):
                return False
            
            if re.search(r"[$@]|\b\d+\.\d{2}\b", text):
                return False
            if re.search(
                r"\b(?:subtotal|gst|total|balance due|paid|invoice|"
                r"payment|bank|account|terms|customer|registration|"
                r"quantity|unit price|amount|website|telephone)\b",
                text, re.I
            ):
                return False
            return len(text) >= 12 and len(text.split()) >= 3

        candidates = []
        for item in items:
            y = item["corrected_y"]
            labelled = None
            for heading in headings:
                gap = y - heading["corrected_y"]
                if 0 < gap < page_height * 0.25 and abs(item["left"] - heading["left"]) < page_width * 0.22:
                    labelled = heading
                    break

            in_lower_left = (
                first_total_y is not None
                and first_total_y - text_height * 1.4 <= y < footer_y
                and item["left"] < notes_right
            )

            if (labelled is not None or in_lower_left) and is_paragraph_line(item):
                candidates.append(item)

        #Join nearby lines in the same paragraph; don't extract isolated labels.
        groups = []
        for item in candidates:
            if (groups and
                item["corrected_y"] - groups[-1][-1]["corrected_y"] <= max(text_height * 3, page_height * 0.035) and
                abs(item["left"] - groups[-1][-1]["left"]) < page_width * 0.20):
                groups[-1].append(item)
            else:
                groups.append([item])

        for group in groups:
            paragraph = clean_text(" ".join(item["text"] for item in group))
            if len(paragraph) >= 45 or (headings and len(paragraph) >= 12):
                if paragraph not in note_sections:
                    note_sections.append(paragraph)

    #Final tidy-up for snippets that OCR pulled from neighbouring columns.
    cleaned_notes = []
    for section in note_sections:
        lines = [line for line in section.splitlines()
                 if classify_note_line(line) != "skip"]
        if lines:
            cleaned_notes.append("\n".join(lines))
    return "\n".join(cleaned_notes)


#TABLE HEADERS


HEADER_NAMES = {
    "part_number": [
        "part number",
        "part no",
        "part #",
        "part code",
        "part id",
        "product code",
        "product id",
        "stock code",
        "item code",
        "item no",
        "item number",
        "sku",
        "code",
    ],

    "description": [
        "item",
        "description",
        "details",
        "items",
        "item description",
        "item details",
        "work description",
        "service description",
        "service / parts",
        "service parts",
        "items / services",
        "work / parts",
        "product description",
        "parts description",
        "part / description",
        "part description",
        "description of work",
        "description of goods",
        "goods description",
        "work carried out",
        "services rendered",
        "work performed",
        "item / work performed",
        "item work performed",
        "item / work",
        "item work"
    ],

    "quantity": [
        "qty",
        "quantity",
        "q ty",
        "units",
        "number of units",
        "qty supplied",
        "qty ordered",
        "qty / hours",
        "qty / hrs",
        "qty hrs",
        "hours / qty",
        "hours/qty",
        "hours qty",
        "hrs / qty",
        "hrs qty",
        "units / hrs",
        "units hrs",
        "hours",
        "hrs",
        "labour hours",
        "labor hours",
        "hours worked"
    ],

    "unit_price": [
        "unit price",
        "unit cost",
        "unit rate",
        "price per unit",
        "price each",
        "unit price each",
        "rate each",
        "each",
        "each price",
        "unit price excl gst",
        "unit price ex gst",
        "price excl gst",
        "price ex gst",
        "hourly rate",
        "labour rate",
        "labor rate",
        "rate",
        "price",
        "cost",
        "unit"
    ],

    "gst": [
        "gst",
        "gst amount",
        "gst value",
        "tax",
        "tax amount",
        "tax value"
    ],

    "total": [
        "total",
        "amount",
        "line total",
        "line amount",
        "item total",
        "total price",
        "total amount",
        "value",
        "extended price",
        "extended amount",
        "amount incl gst",
        "amount inc gst",
        "line total incl gst"
    ]
}


def identify_header(text):
    normalized = normalize_text(text)

    #Percentage headings are still GST columns (e.g. GST (15%)).
    #These are table headers, not GST registration numbers.
    gst_heading = clean_text(re.sub(r"\b(?:nzd|nz|dollars?)\b", "", normalized))
    if re.fullmatch(r"(?:gst|tax)(?:\s+(?:15|10|zero|rate|percent))?", gst_heading):
        return "gst"

    #Reject GST registration details as table headers.
    if re.search(
        r"\b(?:gst|tax)\s*(?:number|no|registration|id)\b",
        normalized
    ):
        return None



    #Allows:
    #Rate (NZD)
    #Amount (NZD)
    #Unit Price NZD
    #Line Total (NZD)
    normalized = re.sub(
        r"\b(?:nzd|nz|dollars?)\b",
        "",
        normalized
    )

    normalized = clean_text(
        normalized
    )

    aliases = []

    for column, names in HEADER_NAMES.items():
        for name in names:
            target = normalize_text(
                name
            )

            aliases.append(
                (
                    target,
                    column
                )
            )

    #Check exact matches first.
    for target, column in aliases:
        if normalized == target:
            return column

    #Then check longer aliases before shorter ones.
    #This prevents:
    #"item work performed"
    #being mistaken for the shorter alias "item".
    aliases.sort(
        key=lambda value: len(value[0]),
        reverse=True
    )

    for target, column in aliases:
        if normalized.startswith(target + " "):
            #Only allow extended headings for descriptive columns.
            #Numeric column headers must match an approved name.
            if column != "description":
                continue

            return column


def infer_missing_columns(items, headers, header_y, median_height):
    """Use aligned numbers when OCR has merged some table headings.

    This is only used for incomplete headings. It checks for repeated
    amounts in the same positions, so single prices in notes/footer
    are not treated as extra invoice columns.
    """
    page_width = max(item["right"] for item in items)
    end = find_table_end(items, header_y)
    body = [
        item for item in items
        if header_y + median_height * 0.7 < item["corrected_y"]
        and (end is None or item["corrected_y"] < end - median_height * 0.5)
        and item["corrected_y"] < header_y + median_height * 50
    ]
    if not body:
        return headers

    amount_pattern = re.compile(
        r"(?:-?\s*\$\s*|\$\s*-?\s*|NZD\s*)?"
        r"\d[\d,]*\.\d{2,3}-?", re.I
    )
    qty_pattern = re.compile(
        r"-?\d+(?:\.\d+)?\s*(?:ea|each|sets?|jobs?|"
        r"hrs?|hours?|litres?|l|kits?|pairs?|pcs|pieces|units?|x)?-?", re.I
    )

    #Separate numeric values by their OCR horizontal position.
    #Repeated positions across several rows indicate a real column.
    tolerance = max(27, median_height * 1.6, page_width * 0.025)

    def aligned_groups(values):
        groups = []
        for item in sorted(values, key=lambda value: value["left"]):
            nearest = None
            for group in groups:
                if abs(item["left"] - median(i["left"] for i in group)) <= tolerance:
                    nearest = group
                    break
            if nearest is None:
                groups.append([item])
            else:
                nearest.append(item)
        groups = [group for group in groups
                  if len(group) >= 3 and
                  len({round(i["corrected_y"] / max(6, median_height)) for i in group}) >= 3]
        return sorted(groups, key=lambda group: median(i["x"] for i in group))

    amounts = aligned_groups([
        item for item in body if amount_pattern.fullmatch(clean_text(item["text"]))
    ])
    if not amounts:
        return headers

    def add_heading(column, group):
        if column in headers:
            return
        headers[column] = {
            "x": median(i["x"] for i in group),
            "left": median(i["left"] for i in group),
            "right": median(i["right"] for i in group),
            "y": header_y,
            "text": "Detected from item positions"
        }

    #Prefer aligning with headings already recognised by PaddleOCR.
    #Only infer the rightmost columns when they repeat on 3+ lines.
    for column in ("total", "gst", "unit_price"):
        if column not in headers:
            continue
        target = headers[column]["x"]
        closest = min(amounts, key=lambda group: abs(median(i["x"] for i in group) - target))
        if abs(median(i["x"] for i in closest) - target) <= page_width * 0.10:
            amounts = [group for group in amounts if group is not closest]

    #Infer numeric columns to the left of known total / rate columns.
    if "unit_price" not in headers and "total" in headers:
        options = [group for group in amounts
                   if median(i["x"] for i in group) < headers["total"]["x"] - tolerance]
        if len(options) >= 2 and "gst" not in headers:
            #With three monetary columns (rate, GST, total), keep
            #the tax separate instead of merging it with the rate.
            add_heading("gst", options[-1])
            add_heading("unit_price", options[-2])
            amounts.remove(options[-1])
            amounts.remove(options[-2])
        elif options:
            add_heading("unit_price", options[-1])
            amounts.remove(options[-1])

    if "total" not in headers and "unit_price" in headers:
        options = [group for group in amounts
                   if median(i["x"] for i in group) > headers["unit_price"]["x"] + tolerance]
        if options:
            add_heading("total", options[-1])
            amounts.remove(options[-1])

    if "total" not in headers and "unit_price" not in headers and len(amounts) >= 2:
        add_heading("total", amounts[-1])
        if len(amounts) >= 3:
            #On tax-column invoices the money columns read:
            #unit price, GST, line total (left to right).
            add_heading("gst", amounts[-2])
            add_heading("unit_price", amounts[-3])
        else:
            add_heading("unit_price", amounts[-2])

    if "quantity" not in headers:
        right_of_qty = headers.get("unit_price", headers.get("total"))
        left_of_qty = headers.get("description")
        if right_of_qty and left_of_qty:
            qty_items = [
                item for item in body
                if qty_pattern.fullmatch(clean_text(item["text"]))
                and not amount_pattern.fullmatch(clean_text(item["text"]))
                and item["x"] > left_of_qty["x"] + page_width * 0.10
                and item["x"] < right_of_qty["x"] - tolerance
            ]
            qty_groups = aligned_groups(qty_items)
            if qty_groups:
                add_heading("quantity", qty_groups[-1])

    return headers


def find_table_columns(items):
    description_candidates = [
        item
        for item in items
        if identify_header(
            item["text"]
        ) == "description"
    ]
    if not description_candidates:
        return {}
    description_item = (
        description_candidates[0]
    )
    median_height = median(
        item["height"]
        for item in items
    )
    tolerance = max(
        25,
        median_height * 2.5
    )
    header_y = (
        description_item[
            "corrected_y"
        ]
    )
    candidates = []
    for item in items:
        difference = abs(
            item["corrected_y"]
            - header_y
        )
        if difference > tolerance:
            continue
        column = identify_header(
            item["text"]
        )
        if column:
            candidates.append(
                (
                    difference,
                    column,
                    item
                )
            )
    headers = {}
    for _, column, item in sorted(
        candidates,
        key=lambda value: value[0]
    ):
        if column in headers:
            continue
       
        headers[column] = {
            "x": item["x"],
            "left": item["left"],
            "right": item["right"],
            "y": item["corrected_y"],
            "text": item["text"]
        }

    if "description" not in headers:
        return {}

    #Some invoices squeeze Qty/Rate/Amount headings together.
    #Recover their columns from repeated numerical positions.
    if not all(column in headers for column in ("quantity", "unit_price", "total")):
        headers = infer_missing_columns(items, headers, header_y, median_height)

    if "total" not in headers and "unit_price" not in headers:
        return {}
    return headers

def find_table_end(items, header_y):
    ending_patterns = [
        r"^subtotal\b",
        r"^sub total\b",
        r"^surtotal\b",  # OCR may read 'subtotal' as 'surtotal'.
        r"^payment summary\b",
        r"^bank details\b",
        r"^grand total\b",
        r"^invoice total\b",
        r"^total payable\b",
        r"^balance due\b",
        r"^amount due\b",
        r"^valid until\b",
        r"^payment advice\b",
        r"^terms\b",
        r"^thank you\b",
        r"^work completed\b",
        r"^estimates given\b",
        r"^notes\b"
    ]

    possible_ends = []

    for item in items:
        if item["corrected_y"] <= header_y:
            continue

        normalized = normalize_text(
            item["text"]
        )

        if any(
            re.search(
                pattern,
                normalized,
                re.I
            )
            for pattern in ending_patterns
        ):
            possible_ends.append(
                item["corrected_y"]
            )

    return (
        min(possible_ends)
        if possible_ends
        else None
    )

def group_table_rows(items):
    if not items:
        return []
    median_height = median(
        item["height"]
        for item in items
    )
    tolerance = max(
        6,
        median_height * 0.70
    )
    rows = []
    for item in sorted(
        items,
        key=lambda i: i["corrected_y"]
    ):
        best_row = None
        best_distance = None
        for row in rows:
            distance = abs(
                item["corrected_y"]
                - row["y"]
            )
            if distance <= tolerance:
                if (
                    best_distance is None
                    or distance < best_distance
                ):
                    best_row = row
                    best_distance = distance
        if best_row is None:
            rows.append({
                "y": item["corrected_y"],
                "items": [item]
            })
        else:
            best_row["items"].append(
                item
            )
            best_row["y"] = (
                sum(
                    i["corrected_y"]
                    for i in best_row["items"]
                )
                / len(best_row["items"])
            )
    return rows




def normalize_quantity(value):
    """
    Standardise OCR quantity formats.

    Examples:
        set 1   -> 1 set
        job 1   -> 1 job
        ea 2    -> 2 ea
        6 L     -> 6 L
        2.5 hrs -> 2.5 hrs
    """
    value = clean_text(value)

    if not value:
        return ""

    #Discounts sometimes use a trailing minus sign (1-).
    if re.fullmatch(r"\d+(?:\.\d+)?-", value):
        return "-" + value[:-1]

    units = r"(?:ea|each|set|sets|job|jobs|hr|hrs|hour|hours|L|litres?|units?|kit|kits|pair|pairs|pcs|pieces)"
    number = r"\d+(?:\.\d+)?"

    #Unit before quantity.
    match = re.fullmatch(
        rf"({units})\s*({number})",
        value,
        re.I
    )

    if match:
        unit, quantity = match.groups()
        return f"{quantity} {unit}"

    #Quantity already before unit.
    match = re.fullmatch(
        rf"({number})\s*({units})",
        value,
        re.I
    )

    if match:
        quantity, unit = match.groups()
        return f"{quantity} {unit}"

    #Keep unfamiliar formats unchanged.
    return value


def get_multiple_invoice_tables(result):
    #Find more than one table heading on the same page
    items = create_items(result)
    if not items:
        return []

    height = median(item["height"] for item in items)
    tolerance = max(10, height * 0.8)
    starts = []

    for item in items:
        if identify_header(item["text"]) != "description":
            continue

        nearby = [
            other for other in items
            if abs(other["corrected_y"] - item["corrected_y"]) <= tolerance
        ]
        columns = {identify_header(other["text"]) for other in nearby}
        columns.discard(None)

        if len(columns) < 3:
            continue
        if "total" not in columns and "unit_price" not in columns:
            continue

        y = item["corrected_y"]
        if not any(abs(y - old) <= tolerance for old in starts):
            starts.append(y)

    starts.sort()
    if len(starts) < 2:
        return []

    tables = []
    tilt = estimate_page_tilt(result)

    for index, start in enumerate(starts):
        end = starts[index + 1] - tolerance if index + 1 < len(starts) else float("inf")
        texts = []
        polygons = []

        for text, polygon in zip(result["rec_texts"], result["rec_polys"]):
            box = get_box_info(polygon)
            y = box["y"] - tilt * box["x"]
            if start - tolerance <= y < end:
                texts.append(text)
                polygons.append(polygon)

        if not texts:
            continue

        part = {"rec_texts": texts, "rec_polys": polygons}
        table = get_invoice_table(part)
        if table.get("rows") and table.get("columns"):
            tables.append({"y": start, "table": table})

    return tables if len(tables) >= 2 else []


def merge_same_page_tables(tables):
    #Separate labelled tables on one page contain different charges.
    #Keep all of them, even if their column layouts differ.
    merged = {"columns": [], "rows": []}
    for entry in tables:
        table = entry["table"]
        for column in table.get("columns", []):
            if column not in merged["columns"]:
                merged["columns"].append(column)
        merged["rows"].extend(table.get("rows", []))
    return merged


def get_multiple_table_blocks(result, tables, sections):
    #Put each table and text section in page order
    items = create_items(result)
    blocks = []

    #Add each table using its position
    for entry in tables:
        table = entry["table"]

        blocks.append((
            entry["y"],
            {
                "type": "table",
                "heading": "",
                "columns": table["columns"],
                "rows": table["rows"]
            }
        ))

    #Add the notes and other text sections
    for section in sections:
        heading_name = normalize_text(section["heading"])
        position = float("inf")

        for item in items:
            if normalize_text(item["text"].rstrip(":")) == heading_name:
                position = min(position, item["corrected_y"])

        blocks.append((
            position,
            {
                "type": "text",
                "heading": section["heading"],
                "content": section["content"]
            }
        ))

    #Sort everything from top to bottom
    blocks.sort(key=lambda entry: entry[0])

    return [block for position, block in blocks]


def clean_table_amount(value):
    #A corrected invoice may have two prices or extra handwritten text.
    #Only fill the cell automatically when there is one clear amount.
    value = clean_text(value)
    if not value:
        return "", ""

    amounts = re.findall(
        r"(?<![\d.])(?:NZD\s*|NZ\$\s*|\$\s*)?"
        r"[+-]?\d[\d,]*\.\d{2,3}-?(?!\d)",
        value, re.I
    )

    #Also catch whole-dollar amounts written next to a corrected price,
    #e.g. a crossed-out $850 followed by $820.00.
    whole_dollars = re.findall(
        r"(?:NZD|NZ\$|\$)\s*[+-]?\d[\d,]*(?![\d.,])",
        value, re.I
    )
    if len(amounts) > 1 or (amounts and whole_dollars):
        return "", "Multiple amounts: " + value
    if len(amounts) == 0:
        return "", "Unclear amount: " + value

    amount = clean_text(amounts[0])
    #Negative charges can be written -$35.00 or $35.00-.
    #Preserve the discount rather than turning it into a charge.
    if re.match(r"^\s*-\s*(?:NZD\s*|NZ\$\s*|\$\s*)", value, re.I):
        amount = "-" + amount.lstrip("-")
    elif amount.endswith("-"):
        amount = "-" + amount[:-1]

    #Text such as 'adjusted' or 'crossed out' still needs a check.
    other_text = clean_text(value.replace(amounts[0], ""))
    if re.search(r"[A-Za-z]", other_text) and not re.fullmatch(
        r"(?:NZD|NZ\$)?", other_text, re.I
    ):
        return amount, "Annotation beside amount: " + value
    return amount, ""


def split_inline_quantity_price(row):
    """Separate 'Oil filter 1 ea $25.00' when OCR merged columns.

    Keep the original description if the pattern is not clear.
    """
    description = clean_text(row.get("description", ""))
    if not description or row.get("quantity") or row.get("unit_price"):
        return row

    match = re.fullmatch(
        r"(.+?)\s+(-?\d+(?:\.\d+)?\s*"
        r"(?:ea|each|sets?|jobs?|hrs?|hours?|litres?|L|kits?|pairs?|pcs|pieces|units?|x)?)"
        r"\s+(\$\s*-?\d[\d,]*\.\d{2,3}-?)",
        description, re.I
    )
    if match:
        row["description"] = match.group(1).strip()
        row["quantity"] = normalize_quantity(match.group(2))
        row["unit_price"] = match.group(3)
    return row


def get_invoice_table(result):
    items = create_items(result)

    if not items:
        return {
            "columns": [],
            "rows": []
        }

    headers = find_table_columns(
        items
    )

    if not headers:
        return {
            "columns": [],
            "rows": []
        }

    ordered_headers = sorted(
        headers.items(),
        key=lambda pair: pair[1]["x"]
    )

    
    print("\n=== TABLE HEADER DEBUG ===")

    for column, header in ordered_headers:
        print(
            f"{column}: "
            f"text={header['text']!r}, "
            f"x={header['x']:.1f}, "
            f"left={header['left']:.1f}"
        )


    columns = [
        column
        for column, _
        in ordered_headers
    ]

    header_y = median(
        info["y"]
        for _, info
        in ordered_headers
    )

    boundaries = []

    for index in range(
        len(ordered_headers) - 1
    ):
        left_x = (
            ordered_headers[
                index
            ][1]["x"]
        )

        right_x = (
            ordered_headers[
                index + 1
            ][1]["x"]
        )

        boundaries.append(
            (left_x + right_x) / 2
        )

    table_end = find_table_end(
        items,
        header_y
    )

    median_height = median(
        item["height"]
        for item in items
    )

    body_start = (
        header_y
        + max(
            8,
            median_height * 0.7
        )
    )

    
    body_items = []

   
    #Find the leftmost table heading.
    table_left = min(
        header["left"]
        for header in headers.values()
    )

    #Allow descriptions to start before the heading.
    left_limit = table_left - max(20, median_height * 1.5)

    #In side-by-side layouts, the description heading can be
    #centred above text that starts further to the left.
    #Keep the other half of the page outside the table.
    page_width = max(item["right"] for item in items)
    if (
        columns[0] == "description"
        and headers["description"]["left"] > page_width * 0.5
    ):
        left_limit = min(left_limit, page_width * 0.48)

    for item in items:
        y = item["corrected_y"]

        #Ignore everything above the table body.
        if y <= body_start:
            continue

        #Ignore everything after the table ends.
        if (
            table_end is not None
            and y >= table_end - median_height * 0.5
        ):
            continue

        #Exclude customer and vehicle information
        #located to the left of the actual table.
        if item["left"] < left_limit:
            continue

        body_items.append(item)

    print("\n=== TABLE BODY DEBUG ===")
    for item in items:
        if body_start < item["corrected_y"] < (table_end or float("inf")):
            print(item["text"], "| LEFT:", item["left"])
    print("LEFT LIMIT:", left_limit)

    grouped_rows = group_table_rows(body_items)

    #Put separately listed items beside their matching prices.
    #Some OCR rows place two descriptions beside one price.
    if "description" in columns and "unit_price" in columns:
        description_column = columns.index("description")
        price_column = columns.index("unit_price")

        def item_column(item):
            return sum(item["x"] > edge for edge in boundaries)

        descriptions = sorted(
            [
                item for group in grouped_rows for item in group["items"]
                if item_column(item) == description_column
                and re.match(r"^\d+(?:\.\d+)?\s*[x×]\s+", item["text"], re.I)
            ],
            key=lambda item: item["corrected_y"]
        )

        priced_rows = sorted(
            [
                group for group in grouped_rows
                if any(
                    item_column(item) == price_column
                    and re.fullmatch(r"\$?\s*\d[\d,]*\.\d{2}", item["text"])
                    for item in group["items"]
                )
            ],
            key=lambda group: group["y"]
        )

        if len(descriptions) == len(priced_rows) and len(descriptions) >= 2:
            for description_item, price_row in zip(descriptions, priced_rows):
                if description_item not in price_row["items"]:
                    for group in grouped_rows:
                        if description_item in group["items"]:
                            group["items"].remove(description_item)
                            break
                    price_row["items"].append(description_item)

            grouped_rows = [group for group in grouped_rows if group["items"]]

            #Vehicle details printed inside the table are not charges.
            first_price_y = priced_rows[0]["y"]
            has_vehicle_info = any(
                re.search(r"\b(?:vehicle details|reg(?:istration)?\s*:)", item["text"], re.I)
                and item["corrected_y"] < first_price_y
                for item in body_items
            )
            if has_vehicle_info:
                grouped_rows = [
                    group for group in grouped_rows
                    if group["y"] >= first_price_y - median_height
                ]

    #Recover part numbers that PaddleOCR placed slightly above or
    #below the rest of their line. Keep correctly grouped codes as-is.
    if "part_number" in columns:
        part_index = columns.index("part_number")

        def column_for_item(item):
            return min(
                sum(item["x"] > edge for edge in boundaries),
                len(columns) - 1
            )

        def has_description(group):
            return any(
                columns[column_for_item(item)] == "description"
                for item in group["items"]
            )

        description_groups = [
            group for group in grouped_rows if has_description(group)
        ]
        description_ys = sorted(group["y"] for group in description_groups)
        row_gaps = [
            b - a for a, b in zip(description_ys, description_ys[1:])
            if b > a
        ]
        typical_gap = median(row_gaps) if row_gaps else median_height * 2.5
        max_offset = max(median_height * 0.9, typical_gap * 0.49)

        for group in grouped_rows:
            if has_description(group):
                continue

            orphan_codes = [
                item for item in group["items"]
                if column_for_item(item) == part_index
                and re.fullmatch(
                    r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*",
                    item["text"]
                )
            ]

            for code in orphan_codes:
                possible = [
                    target for target in description_groups
                    if not any(
                        column_for_item(item) == part_index
                        for item in target["items"]
                    )
                ]
                if not possible:
                    continue

                nearest = min(
                    possible,
                    key=lambda target: abs(target["y"] - code["corrected_y"])
                )
                if abs(nearest["y"] - code["corrected_y"]) <= max_offset:
                    group["items"].remove(code)
                    nearest["items"].append(code)

    table_rows = []

    for row in grouped_rows:
        row_data = {
            column: []
            for column in columns
        }

        for item in sorted(
            row["items"],
            key=lambda i: i["x"]
        ):
            column_index = 0

            for boundary in boundaries:
                if item["x"] > boundary:
                    column_index += 1
                else:
                    break

            column_index = min(
                column_index,
                len(columns) - 1
            )

            column = columns[
                column_index
            ]

            #Long descriptions can reach into the Qty column.
            #Only move words that sit before the Qty heading.
            if (
                column == "quantity"
                and "description" in columns
                and item["right"] < headers["quantity"]["left"]
                and not re.match(r"^\s*\d", item["text"])
            ):
                column = "description"

            row_data[column].append(item)

        cleaned_row = {}

        for column in columns:
            column_items = row_data[column]

            #OCR words on the same line should read left to right.
            #Keep the original order for descriptions across multiple lines.
            if column == "description" and column_items and (
                max(item["corrected_y"] for item in column_items)
                - min(item["corrected_y"] for item in column_items)
            ) < median_height * 0.75:
                column_items = sorted(column_items, key=lambda i: i["left"])
            else:
                column_items = sorted(
                    column_items, key=lambda i: (i["top"], i["left"])
                )

            cleaned_row[column] = clean_text(
                " ".join(item["text"] for item in column_items)
            )
        #Keep a warning when a price was amended or unclear.
        row_warnings = []
        for column in ("unit_price", "gst", "total"):
            if column in cleaned_row and cleaned_row[column]:
                original = cleaned_row[column]
                amount, warning = clean_table_amount(original)
                cleaned_row[column] = amount
                if warning:
                    cleaned_row["raw_" + column] = original
                    row_warnings.append(column + ": " + warning)

        if row_warnings:
            cleaned_row["needs_review"] = True
            cleaned_row["review_reason"] = "; ".join(row_warnings)

        if not any(
            cleaned_row.values()
        ):
            continue

        #Payment summaries and footers are not invoice line items.
        description_text = clean_text(cleaned_row.get("description", ""))
        if re.match(
            r"^(?:sub[ -]?total|surtotal|grand total|invoice total|"
            r"payment summary|bank details|amount payable|total payable)\b",
            description_text, re.I
        ):
            break

        #A paragraph accidentally placed in the quantity column with no
        #clear charge is not a line item; it remains in raw OCR text.
        quantity_text = clean_text(cleaned_row.get("quantity", ""))
        if (len(quantity_text.split()) >= 5
                and not cleaned_row.get("part_number")
                and not cleaned_row.get("total")):
            continue

        #Vehicle information belongs to the invoice header, not its charges.
        if (
            not cleaned_row.get("unit_price")
            and not cleaned_row.get("total")
            and re.search(r"\b(?:vehicle details|reg(?:istration)?\s*:)", cleaned_row.get("description", ""), re.I)
        ):
            continue

        #MERGE MULTILINE DESCRIPTIONS
        #Example:
        #LABOUR  Labour - 100,000 km service...
        #lubrication and scan)
        #The second line has description text but no part
        #number, quantity, price, GST or total. Append it to
        #the previous invoice row instead of creating a new row.

        description = cleaned_row.get(
            "description",
            ""
        )

        other_values = [
            cleaned_row.get(
                column,
                ""
            )
            for column in columns
            if column != "description"
        ]

        is_description_continuation = (
            bool(description)
            and not any(other_values)
            and len(table_rows) > 0
        )

        #A heading between two tables is not a description item.
        if re.fullmatch(
            r"(?:parts?(?:\s+(?:and|&)\s+|\s*[/&]\s*|\s+)"
            r"(?:materials?|labou?r)|parts?|materials?|labou?r)",
            normalize_text(description)
        ) and not any(other_values):
            continue

        if is_description_continuation:
            previous_description = (
                table_rows[-1].get(
                    "description",
                    ""
                )
            )

            table_rows[-1]["description"] = clean_text(
                previous_description
                + " "
                + description
            )

            continue

        #Ignore summary values that accidentally appear
        #without an invoice description.
        #Example:
        #$592.80

        if not description:
            has_part_number = bool(
                cleaned_row.get(
                    "part_number",
                    ""
                )
            )

            has_quantity = bool(
                cleaned_row.get(
                    "quantity",
                    ""
                )
            )

            has_unit_price = bool(
                cleaned_row.get(
                    "unit_price",
                    ""
                )
            )

            if (
                not has_part_number
                and not has_quantity
                and not has_unit_price
            ):
                continue

            
    
  
        #Some invoices put "1 x" inside the description instead of Qty.
        if "quantity" in cleaned_row and not cleaned_row["quantity"]:
            match = re.match(
                r"^(\d+(?:\.\d+)?)\s*[x×]\s+(.+)$",
                cleaned_row.get("description", ""), re.I
            )
            if match:
                cleaned_row["quantity"] = match.group(1)
                cleaned_row["description"] = match.group(2)

        #Standardise quantity formatting before storing the row.
        if "quantity" in cleaned_row:
            cleaned_row["quantity"] = normalize_quantity(
                cleaned_row["quantity"]
            )

        #Unclear quantities are kept for manual checking.
        quantity = clean_text(cleaned_row.get("quantity", ""))
        #Quantity can be a number or a number with a recognised unit.
        #Keep unfamiliar OCR text visible but ask the user to check it.
        valid_quantity = re.fullmatch(
            r"-?\d+(?:\.\d+)?(?:\s*(?:ea|each|sets?|jobs?|hrs?|hours?|"
            r"litres?|l|units?|kits?|pairs?|pcs|pieces|x))?",
            quantity, re.I
        )
        if quantity and not valid_quantity:
            cleaned_row["needs_review"] = True
            old_reason = cleaned_row.get("review_reason", "")
            cleaned_row["review_reason"] = clean_text(
                old_reason + "; Check quantity: " + quantity
            ).lstrip("; ")

        #If OCR kept Qty and Rate in the description box, split
        #only an unambiguous "quantity $rate" ending.
        cleaned_row = split_inline_quantity_price(cleaned_row)

        #OCR can place the final line of a bracketed description first.
        #Fix only this identifiable backwards continuation, not normal rows.
        desc = cleaned_row.get("description", "")
        match = re.match(r"^(and [^)]{5,100}\))\s+(.+\([^)]*)$", desc, re.I)
        if match:
            cleaned_row["description"] = clean_text(match.group(2) + " " + match.group(1))

        #Add each processed invoice row.
        table_rows.append(
            cleaned_row
        )

    
    #Stop if the table starts reading normal paragraphs
    #Only used when there are enough rows to check safely
    if len(table_rows) >= 6:
        valid_rows = 0
        bad_rows = 0
        stop_at = None

        for index, row in enumerate(table_rows):
            quantity = clean_text(row.get("quantity", ""))
            price = clean_text(row.get("unit_price", ""))
            total = clean_text(row.get("total", ""))

            #Normal prices contain numbers, not sentences
            has_amount = bool(
                re.search(r"\d+\.\d{2}", total)
            )

            #Words in number columns can mean OCR has
            #started reading paragraphs below the table
            bad_quantity = len(quantity.split()) >= 5
            bad_price = len(price.split()) >= 5
            bad_total = len(total.split()) >= 5

            looks_like_paragraph = (
                bad_quantity or bad_price or bad_total
            )

            if has_amount and not looks_like_paragraph:
                valid_rows += 1
                bad_rows = 0
                continue

            if valid_rows >= 3 and looks_like_paragraph:
                bad_rows += 1
            else:
                bad_rows = 0

            #Require two suspicious rows in a row
            if bad_rows >= 2:
                stop_at = index - 1
                break

        if stop_at is not None:
            print("Table stopped at row:", stop_at)
            table_rows = table_rows[:stop_at]


    #Return only AFTER every row has been processed.
    return {
        "columns": columns,
        "rows": table_rows
    }


#RECEIPT-STYLE FALLBACK


def get_receipt_style_table(lines):
    """
    Fallback for simple receipts that have no table headers.

    Example:
        Engine oil 5W-30 (5L)        $68.00
        Oil filter (genuine)          $26.50

    Only description + total are returned because receipts like
    this do not provide quantity or unit-price columns.
    """
    rows = []

    #Lines that may contain money but are not invoice items.
    ignored_patterns = (
        r"subtotal|sub total|sub-total|grand total|invoice total|"
        r"estimate total|quote total|total (?:amount )?\(?nzd\)?|"
        r"\b(?:grand )?total(?: amount)?\s*[:\-]?\s*\$|"
        r"gst|tax|balance due|amount due|paid|payment|"
        r"invoice no|invoice number|invoice date|quote no|quote date|"
        r"estimate no|estimate date|customer|vehicle|rego|registration|"
        r"vin|odometer|phone|mobile|email|website|bank|account"
    )

    for line in lines:
        line = clean_text(line)

        if not line:
            continue

        if re.search(
            ignored_patterns,
            line,
            re.I
        ) or re.match(r"^(?:grand\s+)?total(?:\s+amount)?\b", line, re.I):
            continue

        #Require the money amount to be at the END of the line.
        #This avoids grabbing random prices embedded in prose.
        match = re.search(
            r"(?:NZD\s*|NZ\$\s*|\$\s*)"
            r"(\d+(?:,\d{3})*\.\d{2})\s*$",
            line,
            re.I
        )

        if not match:
            continue

        description = clean_text(
            line[:match.start()]
        ).strip(" -:|")

        if not description:
            continue

        #Require some letters so numeric-only lines are ignored.
        if not re.search(
            r"[A-Za-z]",
            description
        ):
            continue

        amount = match.group(1).replace(
            ",",
            ""
        )

        row = {
            "description": description,
            "total": "$" + amount
        }
        row = split_inline_quantity_price(row)

        #Avoid exact duplicate OCR lines.
        if row not in rows:
            rows.append(row)

    if not rows:
        return {
            "columns": [],
            "rows": []
        }

    columns = ["description", "total"]
    if any(row.get("quantity") for row in rows):
        columns.insert(1, "quantity")
    if any(row.get("unit_price") for row in rows):
        columns.insert(-1, "unit_price")
    return {"columns": columns, "rows": rows}

#TABLE PRINTING

def get_column_label(column):
    labels = {
        "part_number": "PART NUMBER",
        "description": "DESCRIPTION",
        "quantity": "QTY",
        "unit_price": "UNIT PRICE",
        "gst": "GST",
        "total": "TOTAL"
    }
    return labels.get(
        column,
        column.upper()
    )

def print_invoice_table(table):
    columns = table["columns"]
    rows = table["rows"]
    if not columns or not rows:
        print(
            "Could not detect invoice table."
        )
        return
    #Minimum widths.
    #These can automatically grow if the invoice contains
    #longer text.
    minimum_widths = {
        "part_number": 14,
        "description": 55,
        "quantity": 10,
        "unit_price": 14,
        "gst": 12,
        "total": 14
    }
    numeric_columns = {
        "quantity",
        "unit_price",
        "gst",
        "total"
    }
    #DYNAMIC COLUMN WIDTHS
    #No extracted text is cut off anymore.
    #Each column expands to fit its longest value.
    widths = {}
    for column in columns:
        label = get_column_label(
            column
        )
        longest_value = max(
            (
                len(
                    clean_text(
                        row.get(
                            column,
                            ""
                        )
                    )
                )
                for row in rows
            ),
            default=0
        )
        minimum_width = minimum_widths.get(
            column,
            15
        )
        widths[column] = max(
            minimum_width,
            len(label) + 2,
            longest_value + 2
        )
    #HEADER
    header = ""
    for column in columns:
        width = widths[column]
        label = get_column_label(
            column
        )
        if column in numeric_columns:
            header += label.rjust(
                width
            )
        else:
            header += label.ljust(
                width
            )
    print(header)
    print(
        "-"
        * sum(
            widths[column]
            for column in columns
        )
    )
    #ROWS
    for row in rows:
        output = ""
        for column in columns:
            width = widths[column]
            value = clean_text(
                row.get(
                    column,
                    ""
                )
            )
            #No truncation here.
            #Full OCR text is always displayed.
            if column in numeric_columns:
                output += value.rjust(
                    width
                )
            else:
                output += value.ljust(
                    width
                )
        print(output)

#MULTI-PAGE TABLE COMBINING

def table_amount_sum(table):
    #Only compare tables when every row has a readable line total.
    #Invoices with discounts or unusual totals are left unchanged.
    amounts = []
    for row in table.get("rows", []):
        value = clean_text(row.get("total", "")).replace(",", "")
        if not re.fullmatch(r"\$?\s*\d+\.\d{2}", value):
            return None
        amounts.append(round(float(value.replace("$", "")), 2))
    if not amounts:
        return None
    return round(sum(amounts), 2)


def get_invoice_review_warnings(table, lines, cost=""):
    warnings = []
    for number, row in enumerate(table.get("rows", []), start=1):
        if row.get("needs_review"):
            warnings.append(f"Row {number}: {row.get('review_reason', 'Check values')}")

    #A missing total is not always an error (e.g. the first page of
    #a continuation invoice), but should be checked by the user.
    if not cost:
        warnings.append("Invoice total not confidently detected")

    if not table.get("rows"):
        warnings.append("No invoice table recognised; review the original image or OCR text")

    #Check arithmetic only when every line total is unambiguous.
    #Do not replace the supplier's printed amount automatically.
    amounts = []
    for row in table.get("rows", []):
        total = clean_text(row.get("total", ""))
        if not re.fullmatch(r"\$?\s*\d[\d,]*\.\d{2}", total):
            amounts = []
            break
        amounts.append(float(total.replace("$", "").replace(",", "")))

    if len(amounts) >= 2:
        subtotal = None
        for line in lines:
            match = re.search(
                r"\bsub[\s-]*total(?:\s*\([^)]*\))?\s*:?\s*"
                r"(?:NZD\s*)?\$?\s*(\d[\d,]*\.\d{2})",
                line, re.I
            )
            if match:
                subtotal = float(match.group(1).replace(",", ""))
                break

        if subtotal is not None:
            gross_sum = sum(amounts)
            #In some tables, line totals include GST but the subtotal
            #excludes it. Account for the per-row GST column if present.
            comparison_sums = [gross_sum]
            if "gst" in table.get("columns", []):
                gst_amounts = []
                for row in table["rows"]:
                    gst = clean_text(row.get("gst", ""))
                    if not re.fullmatch(r"\$?\s*\d[\d,]*\.\d{2}", gst):
                        gst_amounts = []
                        break
                    gst_amounts.append(float(gst.replace("$", "").replace(",", "")))
                if len(gst_amounts) == len(amounts):
                    comparison_sums.append(gross_sum - sum(gst_amounts))

            if min(abs(value - subtotal) for value in comparison_sums) > 0.10:
                warnings.append(
                    f"Printed subtotal {subtotal:.2f} differs from "
                    f"calculated charges; check the original invoice"
                )

    return warnings


def combine_page_tables(page_tables, invoice_total=""):
    #Keep a single combined table for CarMa, but retain each original
    #table inside table["pages"] for the multi-page invoice editor.
    try:
        total = float(clean_text(invoice_total).replace("$", "").replace(",", ""))
    except ValueError:
        total = None

    selected = []
    for page in page_tables:
        table = page["table"]
        if not table.get("rows"):
            continue

        subtotal = table_amount_sum(table)
        matching_index = None

        for index, previous in enumerate(selected):
            previous_table = previous["table"]
            previous_subtotal = table_amount_sum(previous_table)

            #An invoice can show the same charges as a summary and
            #a detailed breakdown on separate pages. Only collapse
            #them if both show a matching subtotal.
            if (subtotal is not None and previous_subtotal is not None
                    and subtotal > 0
                    #The overall invoice total must agree with ONE
                    #breakdown, not both added together.
                    and total is not None
                    and subtotal - 0.02 <= total <= subtotal * 1.25 + 0.02
                    and abs(subtotal - previous_subtotal) <= 0.02
                    and len(table["rows"]) >= 2
                    and len(previous_table["rows"]) >= 2
                    and re.search(r"\bsub\s*total\b", page["raw_text"], re.I)
                    and re.search(r"\bsub\s*total\b", previous["raw_text"], re.I)):
                matching_index = index
                break

        if matching_index is None:
            selected.append(page)
        else:
            previous = selected[matching_index]["table"]
            #Prefer the breakdown with more detail / part numbers.
            old_score = len(previous["rows"]) + sum(
                bool(row.get("part_number")) for row in previous["rows"]
            )
            new_score = len(table["rows"]) + sum(
                bool(row.get("part_number")) for row in table["rows"]
            )
            if new_score > old_score:
                selected[matching_index] = page
            print("Alternative invoice breakdown detected; using the detailed table.")

    combined = {"columns": [], "rows": []}
    for page in selected:
        table = page["table"]
        for column in table["columns"]:
            if column not in combined["columns"]:
                combined["columns"].append(column)
        combined["rows"].extend(table["rows"])
    return combined


#SCAN INVOICE

#STRUCTURED PAGE SECTIONS (ADDITIVE / BACKWARDS COMPATIBLE)


def extract_invoice_sections(result, has_table=False):
    #Use OCR positions to keep text in the correct section
    items = create_items(result)

    if not items:
        return []

    page_width = max(item["right"] for item in items)
    page_height = max(item["bottom"] for item in items)

    #Find where the table starts
    table_start = None
    table_headers = {}

    if has_table:
        table_headers = find_table_columns(items)

    if table_headers:
        table_start = min(
            header["y"] for header in table_headers.values()
        )


    known_headings = {
        "notes", "workshop notes", "mechanic notes",
        "notes work completed", "notes work carried out",
        "notes and work completed", "notes and work carried out",
        "work completed notes", "work carried out notes",
        "workshop notes work completed", "technician notes",
        "mechanics notes",
        "mechanic service notes", "service notes", "repair notes",
        "job notes", "work completed", "work carried out",
        "inspection findings", "recommendations",
        "comments", "remarks", "payment details",
        "terms and conditions", "bank details",
        "bank details for payment", "payment information",
        "additional information", "parts and labour breakdown"
    }

    headings = []

    #Find the section headings
    for item in items:
        text = clean_text(item["text"])
        name = normalize_text(text.rstrip(":"))

        if name in known_headings:
            headings.append((item, text.rstrip(":"), ""))

        else:
            #Sometimes the heading and text are on the same line
            match = re.match(
                r"^(workshop notes|mechanic(?:'s)? notes|work completed|"
                r"inspection findings|recommendations|"
                r"notes?\s*[/&]\s*work (?:completed|carried out)|"
                r"payment details|terms and conditions|"
                r"notes)\s*:\s*(.+)$",
                text,
                re.I
            )

            if match:
                headings.append((item, match.group(1), match.group(2)))

    headings.sort(key=lambda entry: entry[0]["corrected_y"])

    sections = []

    for heading, title, first_line in headings:
        #The table is already saved separately
        if has_table and normalize_text(title) == "parts and labour breakdown":
            continue

        #Find the area underneath this heading
        left = heading["left"] - page_width * 0.04
        right = min(page_width, heading["left"] + page_width * 0.58)
        end_y = float("inf")

        #Notes may be beside a totals or banking panel. Keep each
        #column separate, without restricting full-width notes.
        if heading["left"] < page_width * 0.45:
            right_side_labels = [
                item for item in items
                if item["left"] >= page_width * 0.48
                and -page_width * 0.025 <= (
                    item["corrected_y"] - heading["corrected_y"]
                ) <= page_height * 0.28
                and re.match(
                    r"^(?:sub\s*total|gst(?:\s*\d+%)?|total(?:\s+amount)?|"
                    r"bank(?:\s+details|\s+account)?|"
                    r"account(?:\s+(?:name|number|no))?|"
                    r"reference|payment(?:\s+information)?)\b",
                    normalize_text(item["text"]), re.I
                )
            ]
            if right_side_labels:
                right = min(right, page_width * 0.56)


        #Stop a section if a table starts below it
        if table_start is not None:
            if heading["corrected_y"] < table_start:
                end_y = table_start


        #Stop when another heading starts in the same area
        for next_heading, _, _ in headings:
            if next_heading["corrected_y"] <= heading["corrected_y"]:
                continue

            if left <= next_heading["x"] <= right:
                end_y = min(end_y, next_heading["corrected_y"])

        content = []

        if first_line:
            content.append((
                heading["corrected_y"],
                heading["x"],
                first_line
            ))

        #Collect text from this section's area
        for item in items:
            if item is heading:
                continue

            if not (
                heading["corrected_y"] < item["corrected_y"] < end_y
            ):
                continue

            if not (left <= item["x"] <= right):
                continue

            #Don't copy another section heading into the content
            if any(item is other for other, _, _ in headings):
                continue

            content.append((
                item["corrected_y"],
                item["x"],
                item["text"]
            ))

        content.sort(key=lambda value: (value[0], value[1]))

        #Stop mechanic notes when the invoice footer or payment details begin.
        #Keep separate notes sections and their original wording.
        note_headings = {
            "notes", "workshop notes", "mechanic notes", "mechanics notes",
            "mechanic service notes", "technician notes", "service notes",
            "repair notes", "job notes", "work completed", "work carried out",
            "work completed notes", "work carried out notes",
            "notes work completed", "notes work carried out",
            "notes and work completed", "notes and work carried out",
            "inspection findings", "recommendations", "comments", "remarks"
        }
        if normalize_text(title) in note_headings:
            footer_pattern = (
                r"^(?:thank you\b|thanks for\b|payment due\b|"
                r"payment (?:method|terms|details|information)\b|"
                r"pay by\b|bank (?:transfer|details|account)\b|"
                r"account (?:name|number|no)\b|"
                r"received by\b|authorised signature\b|authorized signature\b|"
                r"terms (?:and|&) conditions\b|"
                r"goods remain (?:the )?property\b|"
                r"all work (?:completed|carried out) to\b|"
                r"page \d+ of \d+\b|generated from workshop\b)"
            )
            for index, (_, _, line) in enumerate(content):
                if re.match(footer_pattern, clean_text(line), re.I):
                    content = content[:index]
                    break

        if content:
            sections.append({
                "heading": title,
                "type": "text",
                "content": "\n".join(value[2] for value in content)
            })

    return sections

def get_invoice_blocks(lines, detected_table, sections):
    #Keep text and the detected table in their OCR order.
    #The older table and sections fields are kept as well.
    blocks = []
    table_rows = detected_table.get("rows", [])
    table_columns = detected_table.get("columns", [])
    has_table = bool(table_rows and table_columns)

    #Find where the table starts on the page.
    table_position = None
    table_heading = ""
    if has_table:
        for index, line in enumerate(lines):
            value = normalize_text(clean_text(line).rstrip(":"))
            if value in ("parts and labour breakdown", "parts and labor breakdown"):
                table_position = index
                table_heading = clean_text(line).rstrip(":")
                break

        if table_position is None:
            for index, line in enumerate(lines):
                value = normalize_text(clean_text(line))
                if (("description" in value or "items" in value) and ("qty" in value or "quantity" in value or "price" in value)):
                    table_position = index
                    break

    #Find text headings on the page so their order is preserved.
    section_positions = []
    used_positions = set()
    for section in sections:
        position = len(lines)
        for index, line in enumerate(lines):
            if index in used_positions:
                continue
            if normalize_text(clean_text(line).rstrip(":")) == normalize_text(section["heading"]):
                position = index
                used_positions.add(index)
                break
        section_positions.append((position, section))

    if has_table:
        blocks.append((table_position if table_position is not None else 0, -1, {
            "type": "table",
            "heading": table_heading,
            "columns": table_columns,
            "rows": table_rows,
        }))

    for index, (position, section) in enumerate(section_positions):
        blocks.append((position, index, {
            "type": "text",
            "heading": section.get("heading", ""),
            "content": section.get("content", ""),
        }))

    blocks.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in blocks]

def get_invoice_content_text(lines):
    #Common headings that indicate the start of invoice content
    content_heading = re.compile(
        r"^(?:"
        r"mechanic(?:'s)? (?:service )?notes?|"
        r"technician(?:'s)? notes?|"
        r"service notes?|"
        r"repair notes?|"
        r"work completed|"
        r"work carried out|"
        r"inspection findings|"
        r"recommendations?|"
        r"parts and labour breakdown"
        r")\s*:?\s*$",
        re.I
    )

    #Look for the first clearly identified content heading
    for index, line in enumerate(lines):
        if content_heading.fullmatch(line.strip()):
            return "\n".join(lines[index:])

    #If uncertain, preserve everything rather than lose content
    return "\n".join(lines)

def scan_invoice(image_paths):
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(
        use_doc_orientation_classify=True,
        use_doc_unwarping=True,
        use_textline_orientation=True,
        lang="en"
    )
    raw_ocr_lines = []
    joined_lines = []
    all_results = []

    combined_table = {
        "columns": [],
        "rows": []
    }

    #Store the separate table from each invoice page.
    page_tables = []

    #Give each page a number starting from 1.
    for page_number, image_path in enumerate(image_paths, start=1):
        results = ocr.predict(
            str(image_path)
        )

        for result in results:
            all_results.append(
                result
            )

            for text in result["rec_texts"]:
                text = clean_text(
                    text
                )

                if text:
                    raw_ocr_lines.append(
                        text
                    )

            joined_lines.extend(
                join_lines(
                    result
                )
            )

            
            detected_table = get_invoice_table(
                result
            )

            #Detect multiple tables, keeping the original single-table fallback
            multiple_tables = get_multiple_invoice_tables(result)
            if multiple_tables:
                detected_table = merge_same_page_tables(multiple_tables)

            #Save this page's table separately.
            #Get all OCR lines detected on this page
            page_lines = join_lines(result)
            #Preserve the complete original OCR text
            page_text = "\n".join(page_lines)
            #Extract the actual invoice content
            content_text = get_invoice_content_text(page_lines)
            #Keep the existing sections and make ordered blocks too
            page_sections = extract_invoice_sections(
                result,
                has_table=bool(detected_table["rows"])
                )
            page_blocks = get_invoice_blocks(
                content_text.splitlines(), detected_table, page_sections
            )
            #Use separate table blocks if more than one was found
            if multiple_tables:
                page_blocks = get_multiple_table_blocks(
                    result,
                    multiple_tables,
                    page_sections
                    )

            #Only add mechanic notes that are not already in a text section.
            page_notes = get_notes_from_results([result], detected_table["rows"])
            shown_lines = []
            for block in page_blocks:
                if block.get("type") == "text":
                    shown_lines.extend(block.get("content", "").splitlines())

            #Compare the text rather than just checking section headings.
            shown_text = [normalize_text(line) for line in shown_lines]
            extra_notes = []
            for line in page_notes.splitlines():
                cleaned = normalize_text(line)
                if not cleaned or cleaned in {
                    "notes", "mechanic notes", "workshop notes",
                    "work completed", "inspection findings", "recommendations",
                    "description", "quantity", "qty", "qty hours", "item"
                } or re.fullmatch(r"\d{1,2}\s+[a-z]+\s+20\d{2}", cleaned):
                    continue

                #Ignore a line if another section already contains it.
                if any(cleaned == existing or (
                    len(cleaned) > 12 and cleaned in existing
                ) for existing in shown_text):
                    continue

                if cleaned not in [normalize_text(note) for note in extra_notes]:
                    extra_notes.append(line)

            if extra_notes:
                page_blocks.append({
                    "type": "text",
                    "heading": "Mechanic Notes",
                    "content": "\n".join(extra_notes)
                })

            #Store the page's table and text
            page_tables.append({
                "page": page_number,
                "table": detected_table,
                "raw_text": page_text,
                "content_text": content_text,
                "notes": page_notes,
                "sections": page_sections,
                "blocks": page_blocks
                })
            
            print("OCR Page:", page_number)
            #Count how many rows detected
            print("Rows detected:",
                  len(detected_table["rows"]))
            #Retrieve Column Headings
            print("Columns Detected",
                  detected_table["columns"])
            print("Detected Items:", detected_table["rows"])
    metadata_lines = (
        raw_ocr_lines
        + joined_lines
    )

    #Combine page tables without counting an alternative breakdown twice.
    combined_table = combine_page_tables(page_tables, get_total(metadata_lines))

    #If no normal invoice table was found, try a simple
    #receipt-style layout such as "Description    $12.34".
    if not combined_table["rows"]:
        combined_table = get_receipt_style_table(
            joined_lines
        )

    raw_text = "\n".join(
        metadata_lines
    )

    #Use the table for categories, with one clear note-based repair case.
    category_text = get_category_text_from_table(combined_table, raw_text)
    notes_for_category = get_notes_from_results(
        all_results, combined_table["rows"]
    )
    notes_one_line = " ".join(notes_for_category.split())
    if re.search(r"\bshocks?\s+replaced\s+due\s+to\s+wear\b", notes_one_line, re.I):
        category_text += "\n" + "shocks replaced due to wear"

    fields = {
        "company_name":
            get_company_from_results(
                all_results,
                metadata_lines
            ),

        "address":
            get_address_from_results(
                all_results
            ),

        "phone_number":
            get_phones_from_results(
                all_results
            ),

        "email_address":
            get_email(
                raw_text
            ),

        "gst_number":
            get_gst(
                metadata_lines
            ),

        "invoice_number":
            get_invoice_number_from_results(
                all_results,
                metadata_lines
            ),

        "invoice_date":
            get_date(
                metadata_lines
            ),

        "cost":
            get_total(
                metadata_lines, combined_table
            ),

        "invoice_odometer":
        get_invoice_odometer(
            joined_lines
            ),

        "category":
            get_category(category_text),

        "notes":
            get_notes_from_results(
                all_results, combined_table["rows"]
            )
    }
    fields["service_description"] = get_service_description(
        combined_table, fields["notes"], fields["category"]
    )

    fields["counts_as_service"] = (
        "Service" in [name.strip() for name in fields["category"].split("/")]
        and counts_as_general_service(combined_table, fields["notes"])
        )

    combined_table["pages"] = page_tables
    combined_table["review_warnings"] = get_invoice_review_warnings(
        combined_table, metadata_lines, fields["cost"]
    )
    fields["review_warnings"] = combined_table["review_warnings"]
    #Tell the upload page when the scan needs a manual check.
    #Existing fields and saved invoice structure stay unchanged.
    if not fields["invoice_number"]:
        fields["review_warnings"].append("Invoice number not detected")
    if not fields["invoice_date"]:
        fields["review_warnings"].append("Invoice date not detected")
    if not fields["company_name"]:
        fields["review_warnings"].append("Company name not detected")
    if not fields["invoice_odometer"]:
        fields["review_warnings"].append("Odometer not detected (may be absent on invoice)")
    if not fields["invoice_number"] and not fields["invoice_date"] and not combined_table["rows"]:
        fields["review_warnings"].append("Document may not be an invoice; confirm before saving")
    fields["needs_review"] = bool(fields["review_warnings"])
    if combined_table["review_warnings"]:
        print("\n=== ITEMS TO CHECK ===")
        for warning in combined_table["review_warnings"]:
            print("-", warning)

    return (
        fields,
        combined_table,
        raw_text
    )

#MAIN

if __name__ == "__main__":
    folder = Path(
        __file__
    ).resolve().parent

    filenames = (
        sys.argv[1:]
        if len(sys.argv) > 1
        else ["test_invoice5v2.png"]
    )

    image_paths = [
        folder / filename
        for filename in filenames
    ]

    for image_path in image_paths:
        if not image_path.is_file():
            print(
                f"\nImage not found:\n{image_path}"
            )
            sys.exit(1)


    fields, table, raw_text = (
        scan_invoice(
            image_paths
        )
    )

    print("OCR pages received:", len(table["pages"]))

    labels = {
        "company_name": "Company Name",
        "address": "Address",
        "phone_number": "Phone Number",
        "email_address": "Email Address",
        "gst_number": "GST / Tax Number",
        "invoice_number": "Invoice Number",
        "invoice_date": "Invoice Date",
        "cost": "Cost",
        "category": "Category"
    }

    print(
        "\nEXTRACTED FIELDS"
    )

    print(
        "----------------\n"
    )

    for key, label in labels.items():
        value = (
            fields.get(key)
            or "[Please enter manually]"
        )

        print(
            f"{label.ljust(18)}: {value}"
        )

    print(
        "\n\nINVOICE CONTENT"
    )

    print(
        "---------------\n"
    )

    print_invoice_table(
        table
    )

    notes = fields.get(
        "notes",
        ""
    )

    if notes:
        print("\n\nNOTES")
        print("-----\n")

        for note in notes.splitlines():
            note = clean_text(
                note
            )

            if note:
                print(note)

    print()