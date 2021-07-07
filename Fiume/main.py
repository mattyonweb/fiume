import Fiume.state_machine as sm
import Fiume.fiume as fm
    
def main():
    options = sm.parser()

    app = fm.Fiume(options)
    app.begin_session()

    
