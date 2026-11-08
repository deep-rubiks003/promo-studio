"""Explicitly supported text models and USD budget rates per million tokens."""
DEFAULT_MODEL='gemini-3.5-flash-lite'
MODELS={
    'gemini-3.5-flash-lite':dict(label='Gemini 3.5 Flash-Lite',input_rate=.30,output_rate=2.50),
    'gemini-3.1-flash-lite':dict(label='Gemini 3.1 Flash-Lite',input_rate=.25,output_rate=1.50),
    # Preserve the existing conservative post-introductory budget ceiling for 3.8.
    'gemini-3.8-flash':dict(label='Gemini 3.8 Flash',input_rate=1.50,output_rate=7.50),
}

def model_rates(model):
    if model not in MODELS:raise ValueError('Choose a supported model from the model selector.')
    row=MODELS[model]
    return row['input_rate'],row['output_rate']
