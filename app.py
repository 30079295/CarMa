import os
import uuid
import tempfile
import json
import resend
import secrets
import hashlib

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from invoice_ocr import scan_invoice

from flask import Flask, render_template, request, redirect, session, jsonify, Response 
from dotenv import load_dotenv
from supabase import create_client, Client
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from io import BytesIO

#Allow Pillow to open iPhone HEIC and HEIF images
register_heif_opener()

#Read the settings from env file.
load_dotenv()
#Email settings
resend.api_key = os.environ.get("RESEND_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM")

#Create the Flask app and make the assets folder available.
app = Flask(
    __name__,
    static_folder="assets",
    static_url_path="/assets"
)

#Format UTC timestamps into New Zealand time
def format_nz_datetime(value):
    if not value:
        return ""

    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    nz_time = value.astimezone(ZoneInfo("Pacific/Auckland"))

    return nz_time.strftime("%d/%m/%Y %I:%M %p")


app.jinja_env.filters["nz_datetime"] = format_nz_datetime

#Flask uses this key to sign the login session cookie.
app.secret_key = os.environ["FLASK_SECRET_KEY"]

#Connect to Supabase database.
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_SECRET_KEY")
)


#Home

@app.route("/")
@app.route("/index.html")
def home():
    return render_template("index.html")


#Login

@app.route("/login", methods=["GET", "POST"])
@app.route("/login.html", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        response = (
            supabase
            .table("users")
            .select("*")  #Get all columns.
            .eq("email", email)  #Only match  email.
            .execute()  #Send the database request.
        )

        if not response.data:
            return render_template("login.html", error="Incorrect email or password. Please try again."), 401

        user = response.data[0]

        #Compare the entered password with the stored password hash.
        if check_password_hash(user["password_hash"], password):

            #Check if the user has verified their email
            if not user.get("email_verified"):
                return render_template("login.html", error="Please verify your email before logging in."), 403
            #Remember who is logged in
            session["user_id"] = user["user_id"]
            session["full_name"] = user["full_name"]
            session["email"] = user["email"]

            #Send the user to the dashboard
            return redirect("/dashboard.html")

        return render_template("login.html", error="Incorrect password. Please try again."), 401

    return render_template("login.html")


#Register
#Generate a email verification token
def create_verification_token():
    #Create a random token to send in the email
    token = secrets.token_urlsafe(32)

    #Hash the token before saving it to Supabase
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    #Verification link expires after 24 hours
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)

    return token, token_hash, expires_at.isoformat()

@app.route("/register.html")
def register_page():
    return render_template("register.html")


@app.route("/register", methods=["POST"])
def register_submit():
    #Read the values submitted by the registration form.
    full_name = request.form["full_name"]
    email = request.form["email"]
    password = request.form["password"]
    confirm_password = request.form["confirm_password"]

    if password != confirm_password:
        return render_template("register.html", error="Passwords do not match. Please try again."), 400

    #Check whether this email already has an account.
    existing_user = (
        supabase
        .table("users")
        .select("user_id")
        .eq("email", email)
        .execute()
    )

    if existing_user.data:
        return render_template("register.html",error="An account with this email already exists."), 400

    #Store a password hash instead of the actual password.
    password_hash = generate_password_hash(password)

    #Create an email verification token
    token, token_hash, expires_at = create_verification_token()

    #Add the new account to the database
    supabase.table("users").insert({
        "full_name": full_name,
        "email": email,
        "password_hash": password_hash,
        "email_verified": False,
        "verification_token_hash": token_hash,
        "verification_expires_at": expires_at
    }).execute()

    #Create a link to verify the email
    verification_link = request.url_root.rstrip("/") + "/verify-email?token=" + token

    
    #Send verification email
    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [email],
            "subject": "Verify your CarMa account",
            "html": (
                "<p>Welcome to CarMa!</p>"
                "<p>Click the button below to verify your email:</p>"
                f'<p><a href="{verification_link}">Verify Email Address</a></p>'
                "<p>This link expires in 24 hours.</p>"
            )
        })

    except Exception:
        app.logger.exception("Verification email failed")
        return render_template("login.html", error="Your account was created, but the verification email could not be sent. Please use Resend Verification Email below."), 503
    return render_template("check-email.html")

#Verify the user's email address
@app.route("/verify-email")
def verify_email():
    #Get the token from the email link
    token = request.args.get("token", "")

    if not token:
        return render_template("login.html", error="Invalid verification link. Please request a new one."), 400
    
    #Hash the token to match the one saved in Supabase
    token_hash = hashlib.sha256(token.encode()).hexdigest()


    #Find the account linked to this token
    response = (
        supabase
        .table("users")
        .select("user_id, verification_expires_at")
        .eq("verification_token_hash", token_hash)
        .eq("email_verified", False)
        .execute()
    )

    if not response.data:
        return render_template("login.html", error="This verification link is invalid or has already been used."), 400


    user = response.data[0]

    #Check whether the link has expired
    expires_at = datetime.fromisoformat(
        user["verification_expires_at"].replace("Z", "+00:00")
    )

    if datetime.now(timezone.utc) > expires_at:
        return render_template("login.html", error="This verification link has expired. Please request a new one."), 400    


    #Mark the account as verified and remove the used token
    supabase.table("users").update({
        "email_verified": True,
        "verification_token_hash": None,
        "verification_expires_at": None
    }).eq("user_id", user["user_id"]).eq(
        "verification_token_hash", token_hash
    ).eq("email_verified", False).execute()
    
    return render_template("verification-success.html")

#Resend verification email
@app.route("/resend-verification", methods=["POST"])
def resend_verification():
    #Get email entered by user
    email = request.form.get("email", "").strip()
    #Find the account using email address
    response = (
        supabase
        .table("users")
        .select("user_id, email_verified, verification_sent_at")
        .eq("email", email)
        .execute()
    )
    #Use the same message whether the account exists or not
    message = "If your account needs verification a new email will be sent."

    #Check if the email exists
    if not response.data:
        return render_template("login.html", success=message)

    user = response.data [0]

    #Check if the email already verified
    if user ["email_verified"]:
        return render_template("login.html", success=message)

    #Check when last verification email was requested
    last_sent = user.get("verification_sent_at")

    #Calculate how many seconds have passed
    if last_sent:
        last_sent = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
        seconds_passed = (datetime.now(timezone.utc) - last_sent).total_seconds()

    #Stop another request if less than 60 seconds have passed
        if seconds_passed < 60:
            return render_template("login.html", success=message)
        

    #Generate a new verification token
    token, token_hash, expires_at = create_verification_token()

    #Save new token to Supabase
    supabase.table("users").update({
        "verification_token_hash": token_hash,
        "verification_expires_at": expires_at,
        "verification_sent_at": datetime.now(timezone.utc).isoformat()
        }).eq("user_id", user["user_id"]).eq("email_verified", False).execute()

    #Create a new verification link
    verification_link = request.url_root.rstrip("/") + "/verify-email?token=" + token
    #Send the new verification email
    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [email],
            "subject": "Verify your CarMa account",
            "html": (
                "<p>Click the link below to verify your CarMa account:</p>"
                f'<p><a href="{verification_link}">Verify Email Address</a></p>'
                "<p>This link expires in 24 hours.</p>"
            )

        })
        
    #Handle errors if the email fails to send
    except Exception:
        app.logger.exception("Resending verification email failed")
        return render_template(
            "login.html",
            error="Unable to send verification email. Please try again later."
        ), 503

    #Return to login page after sending email
    return render_template("login.html", success=message)

    
#Logout

@app.route("/logout")
def logout():
    #Remove the saved login details.
    session.clear()

    return redirect("/login.html")


#Dashboard

@app.route("/dashboard.html")
def dashboard():
    #Require the user to log in before viewing this page.
    if "user_id" not in session:
        return redirect("/login.html")

    #Get only the vehicles belonging to this user.
    vehicles_response = (
        supabase
        .table("vehicles")
        .select("*")
        .eq("user_id", session["user_id"])
        .execute()
    )

    vehicles = vehicles_response.data

    #Count how many vehicles the user has
    total_vehicles = len(vehicles)
    
    #Store vehicle names so invoices can display their vehicle
    vehicle_names = {}
    
    for vehicle in vehicles:
        vehicle_id = str(vehicle["vehicle_id"])
        vehicle_names[vehicle_id] = (
            str(vehicle["year"]) + " " +
            vehicle["make"] + " " +
            vehicle["model"]
            )

    #Get the invoices belonging to the user
    invoice_count_response = (
        supabase
        .table("invoices")
        .select("invoice_id", count="exact", head=True)
        .eq("user_id", session["user_id"])
        .execute()
    )

    total_invoices = invoice_count_response.count or 0

    
    #Count vehicles with upcoming or overdue services
    upcoming_services = 0

    for vehicle in vehicles:
        last_service = vehicle.get("last_service_km")
        interval = vehicle.get("service_interval_km")
        odometer = vehicle.get("odometer")

        if last_service is None or interval is None or odometer is None:
            continue

        if interval <= 0:
            continue

        next_service = last_service + interval
        km_remaining = next_service - odometer

        if km_remaining <= 1000:
            upcoming_services += 1


    #Get the users five most recent invoices by invoice date.
    invoices_response = (
        supabase
        .table("invoices")
        .select("*")
        .eq("user_id", session["user_id"])
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )

    return render_template(
        "dashboard.html",
        full_name=session["full_name"],
        vehicles=vehicles,
        total_vehicles=total_vehicles,
        total_invoices=total_invoices,
        upcoming_services=upcoming_services,
        recent_invoices=invoices_response.data,
        vehicle_names=vehicle_names
    )


#Get available vehicle makes
@app.route("/api/vehicle-makes")
def vehicle_makes():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    search = request.args.get("search", "").strip()[:50]

    response = supabase.rpc("search_vehicle_makes", {
        "search_text": search
    }).execute()

    return jsonify(response.data)


#Get models for the selected make
@app.route("/api/vehicle-models")
def vehicle_models():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    make = request.args.get("make", "").strip()
    search = request.args.get("search", "").strip()[:50]

    if not make:
        return jsonify([])

    response = supabase.rpc("search_vehicle_models", {
        "selected_make": make,
        "search_text": search
    }).execute()

    return jsonify(response.data)


#Get years for the selected make and model
@app.route("/api/vehicle-years")
def vehicle_years():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    make = request.args.get("make", "").strip()
    model = request.args.get("model", "").strip()

    if not make or not model:
        return jsonify([])

    response = supabase.rpc("search_vehicle_years", {
        "selected_make": make,
        "selected_model": model
    }).execute()

    return jsonify(response.data)


#Get vehicle variants and engine details
@app.route("/api/vehicle-variants")
def vehicle_variants():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    make = request.args.get("make", "").strip()
    model = request.args.get("model", "").strip()
    year = request.args.get("year", type=int)

    if not make or not model or year is None:
        return jsonify([])

    response = supabase.rpc("search_vehicle_variants", {
        "selected_make": make,
        "selected_model": model,
        "selected_year": year
    }).execute()

    return jsonify(response.data)



#Add Vehicle

@app.route("/add-vehicle", methods=["GET", "POST"])
@app.route("/add-vehicle.html", methods=["GET", "POST"])
def add_vehicle():
    if "user_id" not in session:
        return redirect("/login.html")

    if request.method == "POST":
        
        #Check the vehicle odometer if entered
        odometer_text = request.form.get("odometer", "").strip()
        odometer = None
        
        if odometer_text:
            if not odometer_text.isdigit() or int(odometer_text) > 2500000:
                return render_template("add-vehicle.html", error="Enter a valid odometer reading."), 400
            
            odometer = int(odometer_text)
            
        #Check the engine capacity if selected
        engine_text = request.form.get("engine_cc", "").strip()
        engine_cc = None
        
        if engine_text:
            if not engine_text.isdigit() or int(engine_text) > 30000:
                return render_template("add-vehicle.html", error="Enter a valid engine capacity."), 400
            
            engine_cc = int(engine_text)


        #Check the vehicle year
        year_text = request.form.get("year", "").strip()
        
        if not year_text.isdigit() or not 1886 <= int(year_text) <= 2100:
            return render_template("add-vehicle.html",  error="Enter a valid vehicle year."), 400

        #Get the uploaded vehicle photo
        vehicle_image = request.files.get("vehicle_image")
        image_url = None

        if vehicle_image and vehicle_image.filename:
            #Check the original image size
            vehicle_image.seek(0, os.SEEK_END)
            image_size = vehicle_image.tell()
            vehicle_image.seek(0)

            if image_size > 5 * 1024 * 1024:
                return render_template("add-vehicle.html", error="Vehicle image must be under 5mb"),400
            
            try:
                with Image.open(vehicle_image) as image:
                    
                    if image.format not in ["JPEG", "PNG", "WEBP"]:
                        raise ValueError ("Invalid image format")
                    
                    #Convert the image to a WebP to lower size
                    image = image.convert("RGB")
                    
                    #Reszie large images without stretching
                    image.thumbnail((1200, 900))
                    
                    #Save the optimised image in memory
                    output = BytesIO()
                    image.save(output, format="WEBP", quality=80)

            except (UnidentifiedImageError, OSError, ValueError):
                return render_template("add-vehicle.html", error="Please upload a valid JPG, PNG or WebP image."), 400

            #Create a unique filename for the WebP image
            file_name = f"{uuid.uuid4()}.webp"

            #Upload the optimised photo to Supabase
            supabase.storage.from_("vehicle-images").upload(
                file_name,
                output.getvalue(),
                {"content-type": "image/webp"}
            )
            #Get the url of the uploaded img
            image_url = supabase.storage.from_("vehicle-images").get_public_url(file_name)
            
        #Check that a transmission was selected
        transmission = request.form.get("transmission", "").strip()
        
        if transmission not in ["Automatic", "Manual"]:
            return render_template("add-vehicle.html", error="Please select a valid transmission."), 400
        
        #Save the vehicle and link it to the logged-in user.
        supabase.table("vehicles").insert({
            "user_id": session["user_id"],
            "make": request.form["make"],
            "model": request.form["model"],
            "year": int(request.form["year"]),
            "submodel": request.form.get("submodel") or None,
            "body_style": request.form.get("body_style") or None,
            "engine_cc": engine_cc,
            "model_code": request.form.get("model_code") or None,
            "registration_plate": request.form["registration_plate"],
            "odometer": odometer,
            "fuel_type": request.form.get("fuel_type") or None,
            "transmission": transmission,
            "vin": request.form.get("vin"),
            "notes": request.form.get("notes"),
            "image_url": image_url
            }).execute()


        return redirect("/dashboard.html")

    return render_template("add-vehicle.html")


#Edit Vehicle
@app.route("/vehicle/<int:vehicle_id>/edit", methods=["GET", "POST"])
def edit_vehicle(vehicle_id):
    if "user_id" not in session:
        return redirect("/login.html")

    #Find the vehicle and check who owns it
    vehicle_response = (
        supabase.table("vehicles")
        .select("*")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not vehicle_response.data:
        return redirect("/dashboard.html?error=vehicle-not-found")

    vehicle = vehicle_response.data[0]

    if request.method == "POST":

        #Check the vehicle year
        year_text = request.form.get("year", "").strip()

        if not year_text.isdigit() or not 1886 <= int(year_text) <= 2100:
            return render_template(
                "edit-vehicle.html",
                vehicle=vehicle,
                error="Enter a valid vehicle year."
            ), 400

        #Check required details
        make = request.form.get("make", "").strip()
        model = request.form.get("model", "").strip()
        registration = request.form.get("registration_plate", "").strip()
        fuel_type = request.form.get("fuel_type", "")
        transmission = request.form.get("transmission", "")

        if not make or not model or not registration:
            return render_template(
                "edit-vehicle.html",
                vehicle=vehicle,
                error="Please fill in all required vehicle details."
            ), 400

        if fuel_type and fuel_type not in ["Petrol", "Diesel", "Hybrid", "Electric"]:
            return render_template(
                "edit-vehicle.html",
                vehicle=vehicle,
                error="Select a valid fuel type."
            ), 400

        if transmission and transmission not in ["Automatic", "Manual"]:
            return render_template(
                "edit-vehicle.html",
                vehicle=vehicle,
                error="Select a valid transmission."
            ), 400
        
        #Check the engine capacity if entered
        engine_text = request.form.get("engine_cc", "").strip()
        engine_cc = None
        
        if engine_text:
            if not engine_text.isdigit() or not 1 <= int(engine_text) <= 30000:
                return render_template("edit-vehicle.html", vehicle=vehicle, error="Enter a valid engine capacity."), 400
            engine_cc = int(engine_text)


        #Keep the existing photo unless it is replaced or removed
        image_url = vehicle.get("image_url")
        old_image_url = image_url
        vehicle_image = request.files.get("vehicle_image")
        remove_image = request.form.get("remove_vehicle_image") == "yes"

        new_file_name = None

        if vehicle_image and vehicle_image.filename:

            #Check the uploaded image size
            vehicle_image.seek(0, os.SEEK_END)
            image_size = vehicle_image.tell()
            vehicle_image.seek(0)

            if image_size > 5 * 1024 * 1024:
                return render_template(
                    "edit-vehicle.html",
                    vehicle=vehicle,
                    error="Vehicle image must be under 5MB."
                ), 400

            #Convert the new image into WebP
            try:
                with Image.open(vehicle_image) as image:
                    if image.format not in ["JPEG", "PNG", "WEBP"]:
                        raise ValueError("Invalid image format")

                    image = image.convert("RGB")
                    image.thumbnail((1200, 900))

                    output = BytesIO()
                    image.save(output, format="WEBP", quality=80)

            except (UnidentifiedImageError, OSError, ValueError):
                return render_template(
                    "edit-vehicle.html",
                    vehicle=vehicle,
                    error="Please upload a valid JPG, PNG or WebP image."
                ), 400

            #Upload the smaller photo to Supabase
            new_file_name = f"{uuid.uuid4()}.webp"

            supabase.storage.from_("vehicle-images").upload(
                new_file_name,
                output.getvalue(),
                {"content-type": "image/webp"}
            )

            image_url = supabase.storage.from_(
                "vehicle-images"
            ).get_public_url(new_file_name)

        elif remove_image:
            image_url = None

        #Update the saved vehicle details
        updated_vehicle = {
            "make": make,
            "model": model,
            "year": int(year_text),
            "submodel": request.form.get("submodel", "").strip() or None,
            "body_style": request.form.get("body_style", "").strip() or None,
            "engine_cc": engine_cc,
            "model_code": request.form.get("model_code", "").strip() or None,
            "registration_plate": registration,
            "fuel_type": fuel_type or None,
            "transmission": transmission or None,
            "vin": request.form.get("vin", "").strip(),
            "notes": request.form.get("notes", "").strip(),
            "image_url": image_url
        }

        try:
            supabase.table("vehicles").update(
                updated_vehicle
            ).eq("vehicle_id", vehicle_id).eq(
                "user_id", session["user_id"]
            ).execute()

        except Exception:
            #Remove the new image if saving the vehicle fails
            if new_file_name:
                supabase.storage.from_("vehicle-images").remove(
                    [new_file_name]
                )
            raise

        #Remove the old photo after saving the changes
        if old_image_url and old_image_url != image_url:
            from urllib.parse import urlparse, unquote

            public_path = "/storage/v1/object/public/vehicle-images/"
            old_path = urlparse(old_image_url).path

            #Only remove files from our vehicle-images bucket
            if public_path in old_path:
                old_file_name = unquote(
                    old_path.split(public_path, 1)[1]
                )

                if old_file_name:
                    try:
                        supabase.storage.from_("vehicle-images").remove(
                            [old_file_name]
                        )
                    except Exception:
                        app.logger.exception(
                            "Could not delete old vehicle photo"
                        )

        return redirect(f"/vehicle/{vehicle_id}")

    return render_template("edit-vehicle.html", vehicle=vehicle)

#Manage Vehicle

@app.route("/vehicle/<int:vehicle_id>")
def manage_vehicle(vehicle_id):
    if "user_id" not in session:
        return redirect("/login.html")

    #Find the vehicle and check that this user owns it.
    vehicle_response = (
        supabase
        .table("vehicles")
        .select("*")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not vehicle_response.data:
        return redirect("/dashboard.html?error=vehicle-not-found")

    vehicle = vehicle_response.data[0]

    #Get the invoices linked to this vehicle.
    invoices_response = (
        supabase
        .table("invoices")
        .select("*")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .order("date", desc=True)
        .execute()
    )

    invoices = invoices_response.data
#Create separate lists for the category tabs.
    service_invoices = []
    repair_invoices = []
    modification_invoices = []
    
    for invoice in invoices:
        categories = invoice.get("category") or []
        
        if "Service" in categories:
            service_invoices.append(invoice)
            
        if "Repair" in categories:
            repair_invoices.append(invoice)
            
        if "Modification" in categories:
            modification_invoices.append(invoice)
            
    return render_template(
            "manage-vehicle.html",
            vehicle=vehicle,
            invoices=invoices,
            service_invoices=service_invoices,
            repair_invoices=repair_invoices,
            modification_invoices=modification_invoices
        )


#Delete Vehicle

@app.route("/vehicle/<int:vehicle_id>/delete", methods=["POST"])
def delete_vehicle(vehicle_id):
    if "user_id" not in session:
        return redirect("/login.html")

    #Check that the vehicle belongs to this user.
    vehicle_response = (
        supabase
        .table("vehicles")
        .select("*")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not vehicle_response.data:
        return redirect("/dashboard.html?error=vehicle-not-found")

    #Get all invoices linked to this vehicle.
    invoices_response = (
        supabase
        .table("invoices")
        .select("invoice_image_url, invoice_image_urls")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )


    #Delete all invoice images linked to this vehicle
    for invoice in invoices_response.data:
        image_paths = invoice.get("invoice_image_urls") or []

        #Support older invoices with one image
        if not image_paths and invoice.get("invoice_image_url"):
            image_paths = [invoice["invoice_image_url"]]

        if image_paths:
            #Find the matching thumbnails
            thumbnail_paths = []

            for image_path in image_paths:
                folder, filename = image_path.rsplit("/", 1)
                thumbnail_paths.append(f"{folder}/thumb_{filename}.webp")

            #Remove thumbnails and original invoice images
            try:
                supabase.storage.from_("invoice-images").remove(thumbnail_paths)
            except Exception:
                app.logger.exception("Failed to delete invoice thumbnails")

            supabase.storage.from_("invoice-images").remove(image_paths)


    #Delete the vehicle's invoices.
    (
        supabase
        .table("invoices")
        .delete()
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    #Delete the vehicle.
    (
        supabase
        .table("vehicles")
        .delete()
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    return redirect("/dashboard.html")


#Update Odometer

@app.route("/vehicle/<int:vehicle_id>/odometer", methods=["POST"])
def update_odometer(vehicle_id):
    if "user_id" not in session:
        return redirect("/login.html")

    #Get the current reading and check ownership.
    vehicle_response = (
        supabase
        .table("vehicles")
        .select("odometer")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not vehicle_response.data:
        return redirect("/dashboard.html?error=vehicle-not-found")
    
    #Read the new reading from the form.
    odometer_text = request.form.get("odometer", "").strip()

    #Accept only a whole number.
    if not odometer_text.isdigit():
        return redirect(f"/vehicle/{vehicle_id}?error=odometer-invalid")

    new_odometer = int(odometer_text)
    current_odometer = vehicle_response.data[0]["odometer"]

    #Do not compare service readings without an odometer
    if current_odometer is None:
        return

    #Maximum vehicle odometer is 2.5 million km
    if new_odometer > 2500000:
        return redirect(f"/vehicle/{vehicle_id}?error=odometer-invalid")



    #Save the new reading.
    (
        supabase
        .table("vehicles")
        .update({"odometer": new_odometer})
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    #Check if a service reminder needs to be sent
    send_service_reminder(vehicle_id, session["user_id"])

    #Reload the vehicle page
    return redirect(f"/vehicle/{vehicle_id}")

  

#Check that an uploaded invoice is a valid image
def valid_invoice_image(invoice_file):
    try:
        invoice_file.seek(0)

        with Image.open(invoice_file) as image:
            if image.format not in ["JPEG", "PNG", "WEBP", "HEIF"]:
                return False

            image.verify()

        return True

    except (UnidentifiedImageError, OSError, ValueError):
        return False

    finally:
        invoice_file.seek(0)


#Convert invoice photos into smaller JPEG images
def prepare_invoice_image(invoice_file, quality=85):
    invoice_file.seek(0)

    with Image.open(invoice_file) as image:
        #Correct photos taken sideways on phones
        image = ImageOps.exif_transpose(image)

        #Resize large images while keeping their proportions
        image.thumbnail((2400, 2400))

        #Convert to JPEG for OCR and browser support
        image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)

    invoice_file.seek(0)
    return output.getvalue()


#Scan Invoice with OCR
@app.route("/scan-invoice", methods=["POST"])
def scan_invoice_route():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    #Get all uploaded invoice images.
    invoice_files = request.files.getlist("invoice_file")

    #Check that at least one image exists.
    if not invoice_files or not any(file.filename for file in invoice_files):
        return jsonify({"error": "No invoice files uploaded"}), 400

    
    #Store the temporary paths for every invoice page.
    temp_paths = []

    try:
        #Loop through each uploaded invoice image.
        for invoice_file in invoice_files:
            if not invoice_file.filename:
                continue

            #Get the file extension from the filename.
            file_extension = invoice_file.filename.rsplit(".", 1)[-1].lower()

            if file_extension not in ["jpg", "jpeg", "png", "webp", "heic", "heif"]:
                return jsonify({"error": "Unsupported invoice image format"}), 400

            #Maximum image size is 10MB per page
            invoice_file.seek(0, os.SEEK_END)
            image_size = invoice_file.tell()
            invoice_file.seek(0)

            if image_size > 10 * 1024 * 1024:
                return jsonify({"error": "Each invoice page must be under 10MB"}), 400

            if not valid_invoice_image(invoice_file):
                return jsonify({"error": "Invalid or corrupted invoice image"}), 400
            
            #Convert the image before sending it to OCR
            try:
                image_data = prepare_invoice_image(invoice_file, quality=90)
            except (UnidentifiedImageError, OSError, ValueError):
                return jsonify({"error": "Could not process invoice image"}), 400
            
            #Save a temporary JPEG for PaddleOCR
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
                 temp_file.write(image_data)
                 temp_paths.append(temp_file.name)

        #Run OCR on every uploaded invoice page.
        fields, table, raw_text = scan_invoice(temp_paths)
        print("Invoice Pages Received", len(table["pages"]))
        return jsonify({
            "company_name": fields.get("company_name", ""),
            "company_address": fields.get("address", ""),
            "company_phone": fields.get("phone_number", ""),
            "company_email": fields.get("email_address", ""),
            "gst_number": fields.get("gst_number", ""),
            "invoice_number": fields.get("invoice_number", ""),
            "date": fields.get("invoice_date", ""),
            "cost": fields.get("cost", ""),
            "invoice_odometer": fields.get("invoice_odometer"),
            "category": fields.get("category", ""),
            "service_description": fields.get("service_description", ""),
            "counts_as_service": fields.get("counts_as_service", False),
            "notes": fields.get("notes", ""),
            "table": table,
            "raw_text": raw_text
        })

    finally:
        #Remove temporary invoice images after scanning
        for temp_path in temp_paths:
            if os.path.exists(temp_path):
                os.remove(temp_path)




@app.route("/upload-invoice/<int:vehicle_id>", methods=["GET", "POST"])
def upload_invoice(vehicle_id):
    if "user_id" not in session:
        return redirect("/login.html")

    #Check that the vehicle belongs to this user.
    vehicle_response = (
        supabase
        .table("vehicles")
        .select("*")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not vehicle_response.data:
        return redirect("/dashboard.html?error=vehicle-not-found")

    vehicle = vehicle_response.data[0]

    if request.method == "POST":

        
        #Get all selected invoice categories.
        categories = request.form.getlist("category")
        
        counts_as_service = (
            "Service" in categories
            and request.form.get("counts_as_service") == "on"
            )

        #Get the service description entered by the user
        service_description = request.form.get("service_description", "").strip()

        #Get the entered odometer amount for this service
        invoice_odometer_text = request.form.get("invoice_odometer", "").strip()

        #Default to no service odometer for non service invoices
        invoice_odometer = None

        #Service invoices still need a description
        if "Service" in categories:
            if not service_description:
                return redirect(f"/upload-invoice/{vehicle_id}?error=service-description")

        #Only check the odometer if one was entered
        if invoice_odometer_text:
            if not invoice_odometer_text.isdigit():
                return redirect(f"/upload-invoice/{vehicle_id}?error=invoice-odometer")
        
            invoice_odometer = int(invoice_odometer_text)
            
            #Maximum invoice odometer is 2.5 million km
            if invoice_odometer > 2500000:
                return redirect(f"/upload-invoice/{vehicle_id}?error=invoice-odometer")

        #Require an odometer if this invoice resets the service reminder
        if counts_as_service and invoice_odometer is None:
            return redirect(f"/upload-invoice/{vehicle_id}?error=service-odometer")

        #Confirm large mileage increases before saving
        if invoice_odometer is not None:
            current_km = vehicle["odometer"]

            if current_km is not None and invoice_odometer > current_km + 20000:
                if request.form.get("confirm_large_odometer") != "on":
                    return redirect(
                        f"/upload-invoice/{vehicle_id}?error=odometer-confirm"
                    )

        #Get the structured OCR table.
        invoice_items_text = request.form.get(
            "invoice_items",
            ""
            )

        try:
            invoice_items = (
                json.loads(invoice_items_text)
                if invoice_items_text
                else {})
        
        except json.JSONDecodeError:
            invoice_items = {}

        invoice_files = request.files.getlist("invoice_file")

        invoice_image_paths = []
        thumbnail_paths = []

        for invoice_file in invoice_files:
            if not invoice_file.filename:
                continue

            #Check the image format before saving
            file_extension = invoice_file.filename.rsplit(".", 1)[-1].lower()
            
            if file_extension not in ["jpg", "jpeg", "png", "webp", "heic", "heif"]:
                print("Rejected invoice format:", repr(invoice_file.filename))
                return redirect(f"/upload-invoice/{vehicle_id}?error=invoice-format")

            #Maximum image size is 10MB per page
            invoice_file.seek(0, os.SEEK_END)
            image_size = invoice_file.tell()
            invoice_file.seek(0)

            if image_size > 10 * 1024 * 1024:
                return redirect(f"/upload-invoice/{vehicle_id}?error=invoice-size")

            if not valid_invoice_image(invoice_file):
                return redirect(f"/upload-invoice/{vehicle_id}?error=invoice-invalid")

        try:
            for invoice_file in invoice_files:
                if not invoice_file.filename:
                    continue

                #Save all invoice photos as JPEG
                file_name = f"{uuid.uuid4()}.jpg"
                invoice_image_path = f"{session['user_id']}/{file_name}"

                
                #Convert and compress the invoice before uploading
                image_data = prepare_invoice_image(invoice_file, quality=85)

                #Upload the original invoice
                supabase.storage.from_("invoice-images").upload(
                    invoice_image_path,
                    image_data,
                    {"content-type": "image/jpeg"}
                )

                invoice_image_paths.append(invoice_image_path)

                #Create a smaller image for Manage Vehicle
                with Image.open(BytesIO(image_data)) as image:
                    image = image.convert("RGB")
                    image.thumbnail((240, 240))

                    thumbnail = BytesIO()
                    image.save(thumbnail, format="WEBP", quality=75)

                #Save thumbnail in the same storage bucket
                thumbnail_path = f"{session['user_id']}/thumb_{file_name}.webp"

                supabase.storage.from_("invoice-images").upload(
                    thumbnail_path,
                    thumbnail.getvalue(),
                    {"content-type": "image/webp"}
                )

                thumbnail_paths.append(thumbnail_path)



        except Exception:
            app.logger.exception("Invoice page upload failed")

            if invoice_image_paths:
                try:
                    supabase.storage.from_("invoice-images").remove(invoice_image_paths)
                except Exception:
                    app.logger.exception("Failed to clean up invoice pages")

            #Remove thumbnails if uploading or saving fails
            if thumbnail_paths:
                try:
                    supabase.storage.from_("invoice-images").remove(thumbnail_paths)
                except Exception:
                    app.logger.exception("Failed to clean up invoice thumbnails")

            return redirect(f"/upload-invoice/{vehicle_id}?error=image-upload-failed")

        invoice_image_path = invoice_image_paths[0] if invoice_image_paths else None
        
        try:
            invoice_result = supabase.table("invoices").insert({
            "vehicle_id": vehicle_id,
            "user_id": session["user_id"],

            "company_name":
                request.form.get("company_name", ""),

            "company_address":
                request.form.get("company_address", ""),

            "company_phone":
                request.form.get("company_phone", ""),

            "company_email":
                request.form.get("company_email", ""),

            "gst_number":
                request.form.get("gst_number", ""),

            "invoice_number":
                request.form.get("invoice_number", ""),

            "date":
                request.form["date"],

            "cost":
                float(request.form["cost"]),

            "category":
                categories,
                "counts_as_service": counts_as_service,

            "ocr_text":
                request.form.get("ocr_text", ""),

            #Save the service description only for Service Invoices
            "service_description":
                service_description if "Service" in categories else None,

            #Save the odometer reading for the service
            "invoice_odometer":
            invoice_odometer,

            "invoice_items":
                invoice_items,

            "invoice_notes": "",

            "invoice_image_url": invoice_image_path,
            "invoice_image_urls": invoice_image_paths

                                }).execute()

        except Exception:
            app.logger.exception("Failed to save invoice")

            if invoice_image_paths:
                try:
                    supabase.storage.from_("invoice-images").remove(invoice_image_paths)
                except Exception:
                    app.logger.exception("Failed to clean up invoice images")

            #Remove thumbnails if uploading or saving fails
            if thumbnail_paths:
                try:
                    supabase.storage.from_("invoice-images").remove(thumbnail_paths)
                except Exception:
                    app.logger.exception("Failed to clean up invoice thumbnails")

            return redirect(f"/upload-invoice/{vehicle_id}?error=invoice-save-failed")

        #Save qualifying service invoices in the history table
        if counts_as_service and invoice_odometer is not None:
            supabase.table("service_reading_history").insert({
                "vehicle_id": vehicle_id,
                "user_id": session["user_id"],
                "invoice_id": invoice_result.data[0]["invoice_id"],
                "service_km": invoice_odometer,
                "source": "invoice"
            }).execute()

        #Automatically increase the vehicle odometer from the invoice
        odometer_updated = False

        if invoice_odometer is not None:
            if vehicle["odometer"] is None or invoice_odometer > vehicle["odometer"]:
                supabase.table("vehicles").update({
                    "odometer": invoice_odometer
                }).eq("vehicle_id", vehicle_id).eq(
                    "user_id", session["user_id"]
                ).execute()

                vehicle["odometer"] = invoice_odometer
                odometer_updated = True


        #Update the service reminder from saved history
        if counts_as_service and invoice_odometer is not None:
            update_service_from_invoices(vehicle_id, session["user_id"])

            #Reset maintenance checks when a newer service is recorded
            last_service = vehicle.get("last_service_km")

            if last_service is None or invoice_odometer > last_service:
                supabase.table("vehicles").update({
                    "check_oil": False,
                    "check_coolant": False,
                    "check_tyres": False,
                    "check_lights": False
                }).eq("vehicle_id", vehicle_id).eq(
                    "user_id", session["user_id"]
                ).execute()


        #Check the service reminder when mileage increases
        if odometer_updated:
            send_service_reminder(vehicle_id, session["user_id"])

        return redirect(f"/vehicle/{vehicle_id}")

    return render_template(
        "upload-invoice.html",
        vehicle=vehicle
    )


#Display a smaller invoice image on Manage Vehicle
@app.route("/invoice/<int:invoice_id>/thumbnail")
def invoice_thumbnail(invoice_id):
    if "user_id" not in session:
        return "Not logged in", 401

    #Check that the invoice belongs to this user
    response = (
        supabase.table("invoices")
        .select("invoice_image_url")
        .eq("invoice_id", invoice_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not response.data:
        return "Invoice not found", 404

    image_path = response.data[0].get("invoice_image_url")

    if not image_path:
        return "Invoice image not found", 404

    #Use the matching thumbnail filename
    folder, filename = image_path.rsplit("/", 1)
    thumbnail_path = f"{folder}/thumb_{filename}.webp"

    try:
        image_data = supabase.storage.from_("invoice-images").download(
            thumbnail_path
        )
        return Response(image_data, mimetype="image/webp")

    except Exception:
        #Older invoices might not have thumbnails yet
        return redirect(f"/invoice/{invoice_id}/image")


#Invoice Viewer

@app.route("/invoice/<int:invoice_id>")
def invoice_viewer(invoice_id):
    if "user_id" not in session:
        return redirect("/login.html")

    #Find the invoice and check that this user owns it.
    invoice_response = (
        supabase
        .table("invoices")
        .select("*")
        .eq("invoice_id", invoice_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not invoice_response.data:
        return redirect("/dashboard.html?error=invoice-not-found")

    invoice = invoice_response.data[0]



    #Get the vehicle linked to this invoice.
    vehicle_response = (
        supabase
        .table("vehicles")
        .select("*")
        .eq("vehicle_id", invoice["vehicle_id"])
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not vehicle_response.data:
        return redirect("/dashboard.html?error=vehicle-not-found")

    vehicle = vehicle_response.data[0]

    return render_template(
    "invoice-viewer.html",
    invoice=invoice,
    vehicle=vehicle
    )

@app.route("/invoice/<int:invoice_id>/image")
@app.route("/invoice/<int:invoice_id>/image/<int:page_index>")
def invoice_image(invoice_id, page_index=0):
    if "user_id" not in session:
        return "Not logged in", 401

    #Find the invoice and make sure it belongs to this user.
    invoice_response = (
        supabase
        .table("invoices")
        .select("*")
        .eq("invoice_id", invoice_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not invoice_response.data:
         return "Invoice not found", 404

    invoice = invoice_response.data[0]

    image_paths = invoice.get("invoice_image_urls") or []

    if not image_paths and invoice.get("invoice_image_url"):
        image_paths = [invoice["invoice_image_url"]]

    if page_index < 0 or page_index >= len(image_paths):
        return "Invoice page not found", 404

    image_path = image_paths[page_index]

    if not image_path:
        return "Invoice image not found", 404

    #Download the private file from Supabase Storage.
    image_data = (
        supabase.storage
        .from_("invoice-images")
        .download(image_path)
    )

    if image_path.endswith(".jpg") or image_path.endswith(".jpeg"):
        image_type = "image/jpeg"
    else:
        image_type = "image/png"
        
    return Response(
        image_data,
        mimetype=image_type
)



#Smaller image preview for invoice editing
@app.route("/invoice/<int:invoice_id>/preview")
@app.route("/invoice/<int:invoice_id>/preview/<int:page_index>")
def invoice_preview(invoice_id, page_index=0):
    if "user_id" not in session:
        return "Not logged in", 401

    #Check invoice ownership
    response = (
        supabase.table("invoices")
        .select("invoice_image_url, invoice_image_urls")
        .eq("invoice_id", invoice_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not response.data:
        return "Invoice not found", 404

    invoice = response.data[0]
    image_paths = invoice.get("invoice_image_urls") or []

    if not image_paths and invoice.get("invoice_image_url"):
        image_paths = [invoice["invoice_image_url"]]

    if page_index < 0 or page_index >= len(image_paths):
        return "Invoice page not found", 404

    #Download original, then create a smaller WebP preview
    image_data = supabase.storage.from_("invoice-images").download(
        image_paths[page_index]
    )

    with Image.open(BytesIO(image_data)) as image:
        image = image.convert("RGB")
        image.thumbnail((900, 1200))

        preview = BytesIO()
        image.save(preview, format="WEBP", quality=75)

    return Response(preview.getvalue(), mimetype="image/webp")



#Delete Invoice

@app.route("/invoice/<int:invoice_id>/delete", methods=["POST"])
def delete_invoice(invoice_id):
    if "user_id" not in session:
        return redirect("/login.html")

    #Check ownership and get the invoice.
    invoice_response = (
        supabase
        .table("invoices")
        .select("*")
        .eq("invoice_id", invoice_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not invoice_response.data:
        return redirect("/dashboard.html?error=invoice-not-found")

    invoice = invoice_response.data[0]

   #Get all saved invoice image paths
    image_paths = invoice.get("invoice_image_urls") or []
    
   
    #Support older invoices with only one image
    if not image_paths and invoice.get("invoice_image_url"):
        image_paths = [invoice["invoice_image_url"]]

    if image_paths:
        #Find the matching thumbnails
        thumbnail_paths = []

        for image_path in image_paths:
            folder, filename = image_path.rsplit("/", 1)
            thumbnail_paths.append(f"{folder}/thumb_{filename}.webp")

        #Remove thumbnails and original invoice images
        try:
            supabase.storage.from_("invoice-images").remove(thumbnail_paths)
        except Exception:
            app.logger.exception("Failed to delete invoice thumbnails")

        supabase.storage.from_("invoice-images").remove(image_paths)

    #Delete the invoice from the database.
    (
        supabase
        .table("invoices")
        .delete()
        .eq("invoice_id", invoice_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    #Update service readings after deleting a Service invoice
    if invoice.get("counts_as_service"):
        update_service_from_invoices(
        invoice["vehicle_id"],
        session["user_id"]
    )

    #Check if deleting the invoice made the service overdue
    send_service_reminder(
        invoice["vehicle_id"],
        session["user_id"]
    )


    return redirect(f"/vehicle/{invoice['vehicle_id']}")

#Edit Invoice
@app.route("/invoice/<int:invoice_id>/edit", methods=["GET", "POST"])
def edit_invoice(invoice_id):
    if "user_id" not in session:
        return redirect("/login.html")

    #Find the invoice and check ownership.
    invoice_response = (
        supabase
        .table("invoices")
        .select("*")
        .eq("invoice_id", invoice_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not invoice_response.data:
        return redirect("/dashboard.html?error=invoice-not-found")

    invoice = invoice_response.data[0]

    #Get the vehicle details for the edit page.
    vehicle_response = (
        supabase
        .table("vehicles")
        .select("*")
        .eq("vehicle_id", invoice["vehicle_id"])
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not vehicle_response.data:
         return redirect("/dashboard.html?error=vehicle-not-found")

    vehicle = vehicle_response.data[0]

    if request.method == "POST":
        #Get all selected invoice categories.
        categories = request.form.getlist("category")

        counts_as_service = (
            "Service" in categories
            and request.form.get("counts_as_service") == "on"
            )

        #Service invoices require a description
        service_description = request.form.get("service_description", "").strip()
        
        if "Service" in categories and not service_description:
            return render_template( "edit-invoice.html",  invoice=invoice, vehicle=vehicle, error="Please enter a service description."), 400
        
        #Get the invoice odometer from the form
        invoice_odometer_text = request.form.get("invoice_odometer", "").strip()
        invoice_odometer = None

        if invoice_odometer_text:
            if not invoice_odometer_text.isdigit():
                return "Enter a valid invoice odometer reading", 400

            invoice_odometer = int(invoice_odometer_text)
            
            #Maximum invoice odometer is 2.5 million km
            if invoice_odometer > 2500000:
                return "Enter a valid invoice odometer reading", 400

        #Require an odometer if this invoice resets the service reminder
        if counts_as_service and invoice_odometer is None:
            return render_template(
                "edit-invoice.html",
                invoice=invoice,
                vehicle=vehicle,
                error="An odometer reading is required when resetting the general service reminder."
            ), 400

        #Check large odometer increases when editing
        if invoice_odometer is not None:
            if vehicle["odometer"] is not None and invoice_odometer > vehicle["odometer"] + 20000:
                if request.form.get("confirm_large_odometer") != "on":
                    return "Please confirm the large odometer increase.", 400


        #Get the edited invoice table from the form
        invoice_items_text = request.form.get("invoice_items", "")

        #Convert the JSON text into a dictionary
        try:
            invoice_items = json.loads(invoice_items_text) if invoice_items_text else {}

        except json.JSONDecodeError:
            return "Invalid invoice table data", 400
        

        #Update the existing invoice with the edited form values.
        (
            supabase
            .table("invoices")
            .update({
                "company_name": request.form.get("company_name",""),
                "company_address": request.form.get("company_address",""),
                "company_phone": request.form.get("phone_number",""),
                "company_email": request.form.get("email_address",""),
                "gst_number": request.form.get("gst_number",""),
                "invoice_number" : request.form.get("invoice_number",""),
                "date": request.form["date"],
                "cost": float(request.form["cost"]),
                "category": categories,
                "counts_as_service": counts_as_service,
                "service_description": request.form.get("service_description", "").strip() if "Service" in categories else None,
                "invoice_odometer": invoice_odometer,
                "ocr_text": request.form.get("ocr_text", ""),
                "invoice_items": invoice_items,
                "invoice_notes": ("" if invoice_items.get("pages") else request.form.get("invoice_notes", ""))})
            .eq("invoice_id", invoice_id)
            .eq("user_id", session["user_id"])
            .execute()
        )

        
        #Automatically increase the vehicle odometer after editing
        odometer_updated = False

        if invoice_odometer is not None:
            if vehicle["odometer"] is None or invoice_odometer > vehicle["odometer"]:
                supabase.table("vehicles").update({
                    "odometer": invoice_odometer
                }).eq("vehicle_id", vehicle["vehicle_id"]).eq(
                    "user_id", session["user_id"]
                ).execute()

                odometer_updated = True

        #Update the service reading linked to this invoice
        history_response = (
            supabase.table("service_reading_history")
            .select("history_id, service_km")
            .eq("invoice_id", invoice_id)
            .eq("user_id", session["user_id"])
            .execute()
        )

        if counts_as_service and invoice_odometer is not None:
            if history_response.data:
                #Only create a new history event when mileage changes
                if history_response.data[0]["service_km"] != invoice_odometer:
                    supabase.table("service_reading_history").delete() \
                        .eq("history_id", history_response.data[0]["history_id"]) \
                        .eq("user_id", session["user_id"]) \
                        .execute()

                    supabase.table("service_reading_history").insert({
                        "vehicle_id": vehicle["vehicle_id"],
                        "user_id": session["user_id"],
                        "invoice_id": invoice_id,
                        "service_km": invoice_odometer,
                        "source": "invoice"
                    }).execute()
            else:
                #Save an invoice newly marked as a service
                supabase.table("service_reading_history").insert({
                    "vehicle_id": vehicle["vehicle_id"],
                    "user_id": session["user_id"],
                    "invoice_id": invoice_id,
                    "service_km": invoice_odometer,
                    "source": "invoice"
                }).execute()
        elif history_response.data:
            #Remove history if the invoice no longer counts
            supabase.table("service_reading_history").delete() \
                .eq("invoice_id", invoice_id) \
                .eq("user_id", session["user_id"]) \
                .execute()
            
        #Recalculate when a qualifying service invoice changes
        if invoice.get("counts_as_service") or counts_as_service:
            update_service_from_invoices(
                vehicle["vehicle_id"],
                session["user_id"]
            )

            #Reset checks if editing creates a newer service
            last_service = vehicle.get("last_service_km")

            if (counts_as_service and invoice_odometer is not None
                and (last_service is None or invoice_odometer > last_service)
                and (not invoice.get("counts_as_service")
                     or invoice.get("invoice_odometer") != invoice_odometer)):

                supabase.table("vehicles").update({
                    "check_oil": False,
                    "check_coolant": False,
                    "check_tyres": False,
                    "check_lights": False
                }).eq("vehicle_id", vehicle["vehicle_id"]).eq(
                    "user_id", session["user_id"]
                ).execute()

        #Check for an overdue service after editing
        if odometer_updated:
            send_service_reminder(vehicle["vehicle_id"], session["user_id"])

        return redirect(f"/invoice/{invoice_id}")

    return render_template(
        "edit-invoice.html",
        invoice=invoice,
        vehicle=vehicle
    )


#Generate a Password reset token
def create_reset_token():
    token = secrets.token_urlsafe(32)
    #Hash the token before storing it
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    #Reset link after 30 minuites
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

    return token, token_hash, expires_at.isoformat()

#Forgot Password
@app.route("/forgot-password.html", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        
        response = supabase.table("users").select("user_id, reset_sent_at").eq("email", email).execute()
        
        if response.data:
           #Get account id
           user = response.data[0]
           #Check when the last reset link was sent
           last_sent = user.get("reset_sent_at")

           if last_sent:
               last_sent = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
               seconds_passed = (datetime.now(timezone.utc) - last_sent).total_seconds()

               #Stop requests if made under the 60sec timeframe
               if seconds_passed < 60:
                   return render_template(
                       "forgot-password.html",
                       success="If an account exists with that email, a password reset link has been sent.")


           #Generate a reset token
           token, token_hash, expires_at = create_reset_token()

           #Save the token to Supabase DB
           supabase.table("users").update({
               "reset_token_hash": token_hash,
               "reset_expires_at": expires_at,
               "reset_sent_at": datetime.now(timezone.utc).isoformat()
            }).eq("user_id",user["user_id"]).execute()

           #Create the password reset link
           reset_link = request.url_root.rstrip("/") + "/reset-password?token=" + token

           #Send the password reset email
           try:
               resend.Emails.send({
                   "from": EMAIL_FROM,
                   "to": [email],
                   "subject": "Reset your CarMa Password",
                   "text": (
                       f"Click the link below to reset your CarMa password:\n\n"
                       f"{reset_link}\n\n"
                       f"This link expires in 30 minutes.\n\n"
                       f"If you didn't request this, you can ignore this email."
                   )
               })

           except Exception:
               app.logger.exception("Password reset email failed")


        else: 
            print("Account not found")

        return render_template("forgot-password.html", success="If an account exists with that email, a password reset link has been sent.")
    
    return render_template("forgot-password.html")

#Reset Password
@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    #Get the token from email link
    token = request.args.get("token", "") if request.method == "GET" else request.form.get("token", "")

    if not token:
        return render_template("forgot-password.html",error="Invalid password reset link. Please request a new one."), 400

    #Hash the token to match the one supabase*
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    #Find the account linked to the token
    response = (
        supabase
        .table("users")
        .select("user_id, reset_expires_at")
        .eq("reset_token_hash", token_hash)
        .execute()
    )
    #Check if the token matches an account
    if not response.data:
     return render_template("forgot-password.html", error="This reset link is invalid or has already been used. Please request a new one."), 400

    user = response.data[0]

    #Check if the reset link has expired
    expires_at = datetime.fromisoformat(
        user["reset_expires_at"].replace("Z", "+00:00")
    )

    if datetime.now(timezone.utc) > expires_at:
        return render_template("forgot-password.html", error="This password reset link has expired. Please request a new one."), 400

        #Save the new password when the form is submitted
    if request.method == "POST":
        #Get both passwords from the form
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        #Check both passwords match
        if password != confirm_password:
            return render_template(
                "reset-password.html",
                token=token,
                error="Passwords do not match"
            ), 400

        #Check the password is not empty
        if not password:
            return render_template(
                "reset-password.html",
                token=token,
                error="Enter a new password"
            ), 400

        #Hash the new password
        password_hash = generate_password_hash(password)

        #Save the password and remove the used reset token
        result = supabase.table("users").update({
            "password_hash": password_hash,
            "reset_token_hash": None,
            "reset_expires_at": None
        }).eq("user_id", user["user_id"]).eq(
            "reset_token_hash", token_hash
        ).gt("reset_expires_at", datetime.now(timezone.utc).isoformat()).execute()

        if not result.data:
            return render_template("forgot-password.html", error="This reset link is invalid or has expired. Please request a new one."), 400

        #Clear the login session
        session.clear()

        return redirect("/login.html")

    #Show the reset password page
    return render_template("reset-password.html", token=token)


#Find the latest valid service reading from history
def update_service_from_invoices(vehicle_id, user_id):

    #Get the vehicle's current odometer
    vehicle_response = (
        supabase.table("vehicles")
        .select("odometer")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", user_id)
        .execute()
    )

    if not vehicle_response.data:
        return

    current_odometer = vehicle_response.data[0]["odometer"]

    #Get manual and automatic service history in order
    response = (
        supabase.table("service_reading_history")
        .select("*")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", user_id)
        .order("created_at").order("history_id")
        .execute()
    )

    last_service = None
    source = None

    #Go through each saved service reading
    for record in response.data:
        km = record["service_km"]

        if km > current_odometer:
            continue

        #Manual readings override previous readings
        if record["source"] == "manual":
            last_service = km
            source = "manual"

        #Automatic invoices only update higher readings
        elif record["source"] == "invoice":
            if last_service is None or km > last_service:
                last_service = km
                source = "invoice"

    #Save the latest valid service reading
    supabase.table("vehicles").update({
        "last_service_km": last_service,
        "last_service_source": source
    }).eq("vehicle_id", vehicle_id).eq(
        "user_id", user_id
    ).execute()  


#Send email when a service is overdue
def send_service_reminder(vehicle_id,user_id):
    #Get the vehicle service details
    response = (
        supabase
        .table("vehicles")
        .select("make, model, registration_plate, odometer, last_service_km, service_interval_km, overdue_email_sent_for_km")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", user_id)
        .execute()
    )
    #Check the vehicle exists
    if not response.data:
        return

    #Get the vehicle information
    vehicle = response.data[0]

    #Get the saved service readings
    last_service = vehicle.get("last_service_km")
    interval = vehicle.get("service_interval_km")

    #Stop if the service details havent been entered
    if last_service is None or interval is None or interval <= 0:
        return

    #Calculate when the next service is due
    next_service = last_service + interval

    #Get the current vehicle odometer
    current_km = vehicle["odometer"]

    if current_km is None:
        return

    #Stop if the service isn't overdue
    if current_km <= next_service:
        return

    #Check if this service reminder has already been sent
    if vehicle.get("overdue_email_sent_for_km") == next_service:
        return

    #Get the user email information
    user_response = (
        supabase
        .table("users")
        .select("email, full_name, email_verified")
        .eq("user_id", user_id)
        .execute()
    )

    #Check that the account exists
    if not user_response.data:
        return

    user = user_response.data[0]

    #Only email verified accounts
    if not user["email_verified"]:
        return

    #Calculate how far overdue the service is
    overdue_km = current_km - next_service

    #Get the vehicle name
    vehicle_name = f"{vehicle['make']} {vehicle['model']}"


        #Send the service reminder email
    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [user["email"]],
            "subject": f"CarMa - Service Overdue: {vehicle_name}",
            "text": (
                f"Hi {user['full_name']},\n\n"
                f"Your {vehicle_name} ({vehicle['registration_plate']}) is overdue for a service.\n\n"
                f"Current Odometer: {current_km:,} km\n"
                f"Service Due At: {next_service:,} km\n"
                f"Distance Overdue: {overdue_km:,} km\n\n"
                f"Please check your vehicle's service history in CarMa.\n\n"
                f"This reminder is based on your chosen service interval."
            )
        })

        #Save which service reminder was sent
        supabase.table("vehicles").update({
            "overdue_email_sent_for_km": next_service
        }).eq("vehicle_id", vehicle_id).eq("user_id", user_id).execute()

    except Exception:
        app.logger.exception("Service reminder email failed")

    



#Service Page
@app.route("/service-reminder-page.html")
def service_reminder():
    if "user_id" not in session:
        return redirect("/login.html")
    
    #Request selected vehice as a number
    vehicle_id = request.args.get("vehicle_id", type=int)
    
    #Prevent Flask from searching without a valid vehicle ID
    if vehicle_id is None:
        return redirect("/dashboard.html?error=vehicle-not-found")
    
    #Find Vehicle with ID if it belongs to user
    vehicle_response =(
        supabase
        .table("vehicles")
          .select("*")
          .eq("vehicle_id", vehicle_id)
          .eq("user_id", session["user_id"])
          .execute()
        )

    if not vehicle_response.data:
        return redirect("/dashboard.html?error=vehicle-not-found")

    #Return the first matching record
    vehicle = vehicle_response.data[0]

    #Retrieve invoices belonging to the selected vehicle
    invoices_response = (
        supabase
        .table("invoices")
        .select("*")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .order("date", desc=True)
        .execute()
    )
    #Create empty list to hold invoices cateogrised as Service
    service_invoices = []

    for invoice in invoices_response.data:
        #Retrieve invoice category field
        categories = invoice.get("category") or []
        #If Service is the category add it to the filtered list
        if "Service" in categories:
            service_invoices.append(invoice)

    #Get the vehicle saved service details
    last_service_km = vehicle.get("last_service_km")
    service_interval_km = vehicle.get("service_interval_km")

    #Start with no calculated next service
    next_service_km = None

    #Only calculate when both service values (Last Service and Service Interval) have been entered
    if last_service_km is not None and service_interval_km is not None:
        if service_interval_km > 0:
            next_service_km = last_service_km + service_interval_km

    #Determine the vehicle current service status
    service_status = "Not Set"
    #Default progress when no service details exist yet
    progress_percent = 0
    #Make sure vehicle without data has no remaining kms to prevent issues
    km_remaining = None



    if next_service_km is not None and vehicle["odometer"] is not None:
        #Calculate kms until the next service
        km_remaining = next_service_km - vehicle["odometer"]

        #Calculate how much of the service interval has been used based on the Current Vehicle Odometer
        km_since_service = vehicle["odometer"] - last_service_km

        #Convert the distance into a percentage for Progress Bar
        progress_percent = (km_since_service / service_interval_km) * 100
        #Keep the progress bar between 0-100% to ensure the progress bar is accurate
        progress_percent = max(0, min(100, round(progress_percent)))


        if km_remaining < 0:
            service_status = "Overdue"

        elif km_remaining == 0:
            service_status = "Due Now"

        elif km_remaining <= 1000:
            service_status = "Due Soon"
        
        else:
             service_status = "On Track"
         
                        


    #Access vehicle info from Supabase
    return render_template(
    "service-reminder-page.html",
    #Send vehicle to HTML
    vehicle=vehicle,
    #Send variables to Service Reminder HTML
    service_invoices=service_invoices,
    next_service_km=next_service_km,
    service_status=service_status,
    progress_percent=progress_percent,
    km_remaining=km_remaining,
    )

#Save the selected vehicle service details
@app.route("/vehicle/<int:vehicle_id>/service-details", methods=["POST"])
def update_service_details(vehicle_id):

    #User logged in check
    if "user_id" not in session:
        return redirect("/login.html")
    
    #Find the selected vehicle and veirfy its owned by the user
    vehicle_response = (
        supabase
        .table("vehicles")
        .select("odometer, last_service_km")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id" , session["user_id"])
        .execute()
    )
    #Check if the vehicle extists in Supabase, if not return a 404 error
    if not vehicle_response.data:
         return redirect("/dashboard.html?error=vehicle-not-found")
    
    #variable to store last service odemeter
    last_service_text = request.form.get("last_service_km","").strip()
    #variable to store intervals between services
    service_interval_text = request.form.get("service_interval_km","").strip()

    #Check that both values contain whole numbers
    if not last_service_text.isdigit() or not service_interval_text.isdigit():
         return redirect(f"/service-reminder-page.html?vehicle_id={vehicle_id}&error=service-invalid")
    #Convert the form values into whole numbers
    last_service_km = int(last_service_text)
    service_interval_km=int(service_interval_text)

    #A Interval between Services must be greater than 0kms
    if service_interval_km <=0:
        return redirect(f"/service-reminder-page.html?vehicle_id={vehicle_id}&error=interval-zero")

    #Get the current odometer from the vehicle record
    current_odometer = vehicle_response.data[0]["odometer"]

    #Get the previous service reading from Supabase
    previous_service_km = vehicle_response.data[0]["last_service_km"]

    #Check that the vehicle has an odometer reading
    if current_odometer is None:
        return redirect(f"/service-reminder-page.html?vehicle_id={vehicle_id}&error=service-invalid")
    
    #Last service cannot be higher than current odometer
    if last_service_km > current_odometer:
        return redirect(f"/service-reminder-page.html?vehicle_id={vehicle_id}&error=service-higher")

    #Maximum service odometer is 2.5mil as no NZ car has reached it
    if last_service_km > 2500000:
        return redirect(f"/service-reminder-page.html?vehicle_id={vehicle_id}&error=service-maximum")
    
    #Maximum interval is 100,000km to prevent issues as intervals for a general maintencance shouldnt be that long
    if service_interval_km> 100000:
        return redirect(f"/service-reminder-page.html?vehicle_id={vehicle_id}&error=interval-maximum")
    
    #Check whether the Last Service Odometer has changed
    service_changed = last_service_km != previous_service_km

    #Store the service values we want to save
    update_data = {
        "last_service_km": last_service_km,
        "manual_service_km": last_service_km,
        "service_interval_km": service_interval_km,
        "last_service_source": "manual"
    }

    #Reset checkboxes if last service odometer changed
    if service_changed:
        update_data["check_oil"] = False
        update_data["check_coolant"] = False
        update_data["check_tyres"] = False
        update_data["check_lights"] = False

    #Update the selected vehicle service details
    (
        supabase
        .table("vehicles")
        .update(update_data)
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

   #Save the manual service reading in the history table
    if service_changed:
        supabase.table("service_reading_history").insert({
            "vehicle_id": vehicle_id,
            "user_id": session["user_id"],
            "service_km": last_service_km,
            "source": "manual"
        }).execute()

    #Check if a service reminder needs to be sent
    send_service_reminder(vehicle_id, session["user_id"])

    #Return to the selected vehicle Service History page
    return redirect(f"/service-reminder-page.html?vehicle_id={vehicle_id}")



#Switch service reminders back to invoice tracking
@app.route("/vehicle/<int:vehicle_id>/service-auto", methods=["POST"])
def enable_auto_service(vehicle_id):
    #Check if the user is logged in
    if "user_id" not in session:
        return redirect("/login.html")

    #Find the vehicle and check ownership
    response = (
        supabase.table("vehicles")
        .select("vehicle_id, odometer")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not response.data:
        return redirect("/dashboard.html?error=vehicle-not-found")

        #Check if there is a Service invoice to use
    invoices_response = (
        supabase.table("invoices")
        .select("category, invoice_odometer, counts_as_service")
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )

    has_service_invoice = False

    #Check for a Service invoice with a valid odometer
    for invoice in invoices_response.data:
        if invoice.get("counts_as_service"):
            km = invoice.get("invoice_odometer")

            if km is not None and 0 <= km <= response.data[0]["odometer"]:
                has_service_invoice = True
                break

    if not has_service_invoice:
        return redirect(f"/service-reminder-page.html?vehicle_id={vehicle_id}&error=no-service-invoice")    

     #Enable automatic service tracking
    supabase.table("vehicles").update({
        "last_service_source": "invoice"
    }).eq("vehicle_id", vehicle_id).eq(
        "user_id", session["user_id"]
    ).execute()

    #Find the most recent service from the saved invoices
    update_service_from_invoices(vehicle_id, session["user_id"])

    #Reload the Service Reminder page
    return redirect(f"/service-reminder-page.html?vehicle_id={vehicle_id}")




#Flash route for saving maintencance checkboxes state

@app.route("/vehicle/<int:vehicle_id>/maintenance-check", methods=["POST"])
def update_maintenance_check(vehicle_id):
    #Check whether the user is logged in
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    #Read the checkbox data
    data = request.get_json(silent=True) or {}
    field = data.get("field")

    #Send field and if its checked or not
    checked = data.get("checked")

    #Only allow the following checkbox colums
    allowed_fields = [
        "check_oil",
        "check_coolant",
        "check_tyres",
        "check_lights"
    ]
    #Validate the checkbox field and its value
    if field not in allowed_fields or not isinstance(checked, bool):
        return jsonify({"error": "Invalid checkbox data"}), 400

    response = (
        supabase
        .table("vehicles")
        .update({field: checked})
        .eq("vehicle_id", vehicle_id)
        .eq("user_id", session["user_id"])
        .execute()
    )
    #Check if the vehicle was found and updated
    if not response.data:
        return jsonify({"error": "Vehicle not found"}), 404

    #Tell JS the checkbox was saved succesfully
    return jsonify ({"success": True}), 200

@app.route("/account-settings.html")
def account_settings():
    if "user_id" not in session:
        return redirect("/login.html")

    #Get the users account details from SP DB
    response = (
        supabase
        .table("users")
        .select("full_name, email")
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not response.data:
        session.clear()
        return render_template("login.html", error="Your account could not be found. Please log in again."), 404

    #Send account details to HTML page
    user = response.data[0]

    return render_template("account-settings.html", user=user)

#Update Account Name
@app.route("/account/update-name", methods=["POST"])
def update_account_name():
    #Check if user in session
    if "user_id" not in session:
        return redirect("/login.html")

    #Get the full name entered
    full_name = request.form.get("full_name", "").strip()

    if not full_name:
        return redirect("/account-settings.html?error=name-empty")
    
    #Update the name in Supabase
    response = (
        supabase
        .table("users")
        .update({"full_name": full_name})
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not response.data:
            return redirect("/account-settings.html?error=name-failed")
    
    #Update the name saved in the session
    session["full_name"] = full_name

    return redirect("/account-settings.html")

#Update Account Password
@app.route("/account/update-password", methods=["POST"])
def update_account_password():
    if "user_id" not in session:
        return redirect("/login.html")

    #Get the entered passwords
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    #Check that all fields are filled in
    if not current_password or not new_password or not confirm_password:
        return redirect("/account-settings.html?error=password-empty")

    #Check the new passwords match
    if new_password != confirm_password:
        return redirect("/account-settings.html?error=password-mismatch")

    if len(new_password) < 8:
        return redirect("/account-settings.html?error=password-short")

    #Find the logged in user's password
    response = (
        supabase
        .table("users")
        .select("password_hash")
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not response.data:
        session.clear()
        return render_template("login.html", error="Your account could not be found. Please log in again."), 404

    user = response.data[0]

    #Check that the current password is correct
    if not check_password_hash(user["password_hash"], current_password):
        return redirect("/account-settings.html?error=incorrect-password")

    #Hash and save the new password
    password_hash = generate_password_hash(new_password)

    result = (
        supabase
        .table("users")
        .update({
            "password_hash": password_hash,
            "reset_token_hash": None,
            "reset_expires_at": None
        })
        .eq("user_id", session["user_id"])
        .eq("password_hash", user["password_hash"])
        .execute()
    )

    if not result.data:
        return redirect("/account-settings.html?error=password-failed")

    #Require the user to log in with the new password
    session.clear()

    return redirect("/login.html")

#Change Account Email
@app.route("/account/change-email", methods=["POST"])
def change_account_email():

    #Check if user is logged in
    if "user_id" not in session:
        return redirect("/login.html")

    #Get the new email from the form
    new_email = request.form.get("new_email", "").strip().lower()

    if not new_email or "@" not in new_email:
        return redirect("/account-settings.html?error=email-invalid")

    #Get the user's account details
    response = (
        supabase
        .table("users")
        .select("email, email_change_sent_at, password_hash")
        .eq("user_id", session["user_id"])
        .execute()
    )

    if not response.data:
        session.clear()
        return render_template("login.html", error="Your account could not be found. Please log in again."), 404

    user = response.data[0]

    #Check the user's password before changing email
    current_password = request.form.get("email_change_password", "")
    
    if not check_password_hash(user["password_hash"], current_password):
        return redirect("/account-settings.html?error=email-password")

    #Check if the new email is the same as the current email
    if new_email == user["email"].lower():
        return redirect("/account-settings.html?error=email-same")

    #Check if another account already uses this email
    existing = (
        supabase
        .table("users")
        .select("user_id")
        .eq("email", new_email)
        .execute()
    )

    if existing.data:
        return redirect("/account-settings.html?error=email-exists")

    #Stop repeated requests within 60 seconds
    last_sent = user.get("email_change_sent_at")

    if last_sent:
        last_sent = datetime.fromisoformat(last_sent.replace("Z", "+00:00"))
        seconds_passed = (datetime.now(timezone.utc) - last_sent).total_seconds()

        if seconds_passed < 60:
            return redirect("/account-settings.html?error=email-wait")

    #Generate a random verification token
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    #Verification link expires after 30 minutes
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

    #Create the verification link
    verification_link = request.url_root.rstrip("/") + "/verify-new-email?token=" + token
    
    #Save the pending email and hashed token
    supabase.table("users").update({
        "pending_email": new_email,
        "email_change_token_hash": token_hash,
        "email_change_expires_at": expires_at.isoformat(),
        "email_change_sent_at": datetime.now(timezone.utc).isoformat()
    }).eq("user_id", session["user_id"]).execute()

        #Send the verification email
    try:
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": [new_email],
            "subject": "Verify your new CarMa email address",
            "text": (
                f"Click the link below to verify your new email:\n\n"
                f"{verification_link}\n\n"
                f"This link expires in 30 minutes.\n\n"
                f"If you did not request this change, ignore this email."
            )
        })

    except Exception:
        app.logger.exception("Email change verification failed")

        #Clear the failed email change request
        supabase.table("users").update({
            "pending_email": None,
            "email_change_token_hash": None,
            "email_change_expires_at": None,
            "email_change_sent_at": None
        }).eq("user_id", session["user_id"]).eq(
            "email_change_token_hash", token_hash
        ).execute()

        return redirect("/account-settings.html?error=email-send")

    return redirect("/account-settings.html?success=email-sent")

#Verify the new account email
@app.route("/verify-new-email")
def verify_new_email():
    #Get token from verification link
    token = request.args.get("token", "")

    if not token:
        return render_template("login.html", error="Invalid verification link. Please request a new one."), 400

    #Hash token to match Supabase
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    #Find account linked to token
    response = (
        supabase
        .table("users")
        .select("user_id, pending_email, email_change_expires_at")
        .eq("email_change_token_hash", token_hash)
        .execute()
    )

    if not response.data:
        return render_template("login.html",error="This email verification link is invalid or has already been used."), 400

    user = response.data[0]

    #Check token expiry
    expires_at = datetime.fromisoformat(
        user["email_change_expires_at"].replace("Z", "+00:00")
    )

    if datetime.now(timezone.utc) > expires_at:
        return render_template("login.html",error="This email change verification link has expired. Please request a new one from Account Settings."), 400

    #Check if email is already registered
    existing = (
        supabase
        .table("users")
        .select("user_id")
        .eq("email", user["pending_email"])
        .execute()
    )

    if existing.data:
        return render_template("login.html", error="This email address is already registered. Please choose another."), 400

    #Update account email after verification
    result = (
        supabase
        .table("users")
        .update({
            "email": user["pending_email"],
            "pending_email": None,
            "email_change_token_hash": None,
            "email_change_expires_at": None,
            "email_change_sent_at": None
        })
        .eq("user_id", user["user_id"])
        .eq("email_change_token_hash", token_hash)
        .gt("email_change_expires_at", datetime.now(timezone.utc).isoformat())
        .execute()
    )

    if not result.data:
        return render_template("login.html", error="This email verification link is invalid or has expired. Please request a new one."), 400

    #Log out so user signs in with new email
    session.clear()

    return redirect("/login.html")




#Delete Account
@app.route("/account/delete", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return redirect("/login.html")
    
    user_id = session["user_id"]
    password = request.form.get("delete_password", "")

    #Find the user's password
    response = (
        supabase.table("users")
        .select("password_hash")
        .eq("user_id", user_id)
        .execute()
    )

    if not response.data:
        session.clear()
        return render_template("login.html", error="Your account could not be found. Please log in again."), 404

    #Check password before deleting
    if not check_password_hash(response.data[0]["password_hash"], password):
        return redirect("/account-settings.html?error=delete-password")

    try:
        #Get all invoice image paths
        invoices = (
            supabase.table("invoices")
            .select("invoice_image_url, invoice_image_urls")
            .eq("user_id", user_id)
            .execute()
        )

        invoice_paths = []

        for invoice in invoices.data:
            paths = list(invoice.get("invoice_image_urls") or [])

            if invoice.get("invoice_image_url"):
                paths.append(invoice["invoice_image_url"])

            for path in paths:
                if path and path not in invoice_paths:
                    invoice_paths.append(path)

        #Get all vehicle photos
        vehicles = (
            supabase.table("vehicles")
            .select("image_url")
            .eq("user_id", user_id)
            .execute()
        )

        vehicle_paths = []

        for vehicle in vehicles.data:
            image_url = vehicle.get("image_url")

            if image_url and "/vehicle-images/" in image_url:
                image_path = image_url.split("/vehicle-images/", 1)[1].split("?", 1)[0]
                vehicle_paths.append(image_path)

        #Delete uploaded invoice images
        for path in invoice_paths:
            supabase.storage.from_("invoice-images").remove([path])

        #Delete uploaded vehicle photos
        for path in vehicle_paths:
            supabase.storage.from_("vehicle-images").remove([path])

        #Delete user and related database records
        result = (
            supabase.table("users")
            .delete()
            .eq("user_id", user_id)
            .execute()
        )

        if not result.data:
            return redirect("/account-settings.html?error=delete-failed")

    except Exception:
        app.logger.exception("Account deletion failed")
        return redirect("/account-settings.html?error=delete-failed")

    #Log out after deleting account
    session.clear()

    return redirect("/login.html")



#Start the server when you run: python app.py
if __name__ == "__main__":
    app.run(
        debug=True,
        use_reloader=False,
        threaded=False
    )