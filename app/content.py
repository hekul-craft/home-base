"""Static content: vendor categories, the renter-to-buyer checklist and
seasonal home-care tips shown in the client portal."""

VENDOR_CATEGORIES = [
    "Plumbing",
    "Electrical",
    "HVAC",
    "Roofing",
    "Handyman",
    "Appliance Repair",
    "Pest Control",
    "Landscaping",
    "Cleaning",
    "Painting",
    "Flooring",
    "Windows & Doors",
    "Garage Doors",
    "Locksmith",
    "Pool & Spa",
    "Movers",
    "Home Inspection",
    "Mortgage Lender",
    "Insurance",
    "Title & Escrow",
    "Other",
]

CLIENT_TYPES = {
    "buyer": "Past buyer / homeowner",
    "renter": "Renter",
    "owner": "Property owner / landlord",
}

REQUEST_STATUSES = ["new", "assigned", "scheduled", "completed", "cancelled"]
OPEN_STATUSES = ("new", "assigned", "scheduled")
URGENCIES = ["low", "normal", "urgent"]

# Path-to-homeownership checklist for renters. `category` links a step to
# vendors in the directory that can help (e.g. lenders for pre-approval).
READINESS_STEPS = [
    {
        "key": "credit",
        "title": "Check your credit report and score",
        "help": "Pull your free reports at annualcreditreport.com and dispute any errors. "
        "Most loan programs look for 620+, and higher scores mean lower rates.",
    },
    {
        "key": "budget",
        "title": "Set a comfortable monthly housing budget",
        "help": "A common guideline is to keep housing costs (mortgage, taxes, insurance, HOA) "
        "under about 28% of gross monthly income.",
    },
    {
        "key": "debt",
        "title": "Pay down high-interest debt",
        "help": "Lenders look at your debt-to-income ratio. Lowering card balances helps "
        "both your score and how much you can borrow.",
    },
    {
        "key": "savings",
        "title": "Start a down-payment and closing-cost fund",
        "help": "Down payments can range from 3% to 20%. Plan for closing costs of roughly "
        "2-5% of the price, plus a cushion for move-in and repairs.",
    },
    {
        "key": "assistance",
        "title": "Explore first-time buyer assistance programs",
        "help": "Many states and cities offer down-payment grants or low-rate loans. "
        "Ask us which ones you may qualify for.",
    },
    {
        "key": "documents",
        "title": "Gather your financial documents",
        "help": "Two years of W-2s/tax returns, recent pay stubs, and two months of bank "
        "statements will speed up pre-approval.",
    },
    {
        "key": "preapproval",
        "title": "Get pre-approved with a lender",
        "help": "A pre-approval letter shows sellers you're serious and tells you your "
        "real price range.",
        "category": "Mortgage Lender",
    },
    {
        "key": "wishlist",
        "title": "Write down your must-haves and target neighborhoods",
        "help": "Bedrooms, commute, schools, yard, and deal-breakers. It makes touring "
        "homes much faster.",
    },
    {
        "key": "consult",
        "title": "Book a buyer consultation with us",
        "help": "We'll walk through the process, timeline, and what to expect from offer "
        "to closing - no pressure.",
    },
]
READINESS_KEYS = {step["key"] for step in READINESS_STEPS}

# Seasonal maintenance reminders keyed by month (1-12); categories link to vendors.
SEASONAL_TIPS = {
    "winter": {
        "months": (12, 1, 2),
        "tips": [
            ("Replace furnace filters and keep vents clear", "HVAC"),
            ("Protect pipes from freezing; know where your main water shut-off is", "Plumbing"),
            ("Test smoke and carbon-monoxide detectors", None),
            ("Check the attic for ice dams or leaks after storms", "Roofing"),
        ],
    },
    "spring": {
        "months": (3, 4, 5),
        "tips": [
            ("Schedule an A/C tune-up before the heat arrives", "HVAC"),
            ("Clean gutters and downspouts", "Roofing"),
            ("Inspect the roof for winter damage", "Roofing"),
            ("Book a pest inspection as insects become active", "Pest Control"),
        ],
    },
    "summer": {
        "months": (6, 7, 8),
        "tips": [
            ("Service your irrigation and check for leaks", "Landscaping"),
            ("Touch up exterior paint and caulk around windows", "Painting"),
            ("Clean the dryer vent to reduce fire risk", "Appliance Repair"),
            ("Check the pool or spa equipment", "Pool & Spa"),
        ],
    },
    "fall": {
        "months": (9, 10, 11),
        "tips": [
            ("Schedule a furnace inspection before the first cold night", "HVAC"),
            ("Clean gutters after the leaves drop", "Roofing"),
            ("Winterize outdoor faucets and sprinklers", "Plumbing"),
            ("Seal drafts around doors and windows", "Windows & Doors"),
        ],
    },
}


def season_for_month(month: int) -> tuple[str, list]:
    for name, season in SEASONAL_TIPS.items():
        if month in season["months"]:
            return name, season["tips"]
    raise ValueError(f"invalid month: {month}")
