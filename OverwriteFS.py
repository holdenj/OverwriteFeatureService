# Import system modules
import urllib, urllib2, json
import sys, os
import requests
import arcpy
import ConfigParser
from xml.etree import ElementTree as ET


def urlopen(url, data=None):
    referer = "https://www.arcgis.com/"
    req = urllib2.Request(url)
    req.add_header('Referer', referer)

    if data:
        response = urllib2.urlopen(req, data)
    else:
        response = urllib2.urlopen(req)

    return response


def gentoken(inputUsername, inputPswd, expiration=60):
    #Re-usable function to get a token required for Admin changes
    
    referer = "https://www.arcgis.com/"
    query_dict = {'username': inputUsername,
                  'password': inputPswd,
                  'expiration': str(expiration),
                  'client': 'referer',
                  'referer': referer,
                  'f': 'json'}   
    
    query_string = urllib.urlencode(query_dict)
    url = "https://www.arcgis.com/sharing/rest/generateToken"
    
    token = json.loads(urllib.urlopen(url + "?f=json", query_string).read())
    
    if "token" not in token:
        print(token['messages'])
        sys.exit()
    else:            
        return token['token']    
    

def makeSD(MXD, serviceName, tempDir, outputSD):
    #
    # create a draft SD and modify the properties to overwrite an existing FS
    #    
    
    arcpy.env.overwriteOutput = True
    # All paths are built by joining names to the tempPath
    SDdraft = os.path.join(tempDir, "tempdraft.sddraft")
    newSDdraft = os.path.join(tempDir, "updatedDraft.sddraft")    
     
    arcpy.mapping.CreateMapSDDraft(MXD, SDdraft, serviceName, "MY_HOSTED_SERVICES")
    
    # Read the contents of the original SDDraft into an xml parser
    doc = ET.parse(SDdraft)
    
    if doc.getroot().tag != "SVCManifest":
        raise ValueError("Root tag is incorrect. Is {} a .sddraft file?".format(SDdraft))
    
    # The following 5 code pieces modify the SDDraft from a new MapService
    # with caching capabilities to a FeatureService with Query,Create,
    # Update,Delete,Uploads,Editing capabilities. The first two code
    # pieces handle overwriting an existing service. The last three pieces
    # change Map to Feature Service, disable caching and set appropriate
    # capabilities. You can customize the capabilities by removing items.
    # Note you cannot disable Query from a Feature Service.
    doc.find("./Type").text = "esriServiceDefinitionType_Replacement" 
    doc.find("./State").text = "esriSDState_Published"
    
    # Change service type from map service to feature service
    for config in doc.findall("./Configurations/SVCConfiguration/TypeName"):
        if config.text == "MapServer":
            config.text = "FeatureServer"
    
    #Turn off caching
    for prop in doc.findall("./Configurations/SVCConfiguration/Definition/" +
                                "ConfigurationProperties/PropertyArray/" +
                                "PropertySetProperty"):
        if prop.find("Key").text == 'isCached':
            prop.find("Value").text = "false"
    
    #Turn on feature access capabilities
    for prop in doc.findall("./Configurations/SVCConfiguration/Definition/Info/PropertyArray/PropertySetProperty"):
        if prop.find("Key").text == 'WebCapabilities':
            prop.find("Value").text = "Query,Create,Update,Delete,Uploads,Editing"
    
    # Add the namespaces which get stripped, back into the .SD
    root_elem = doc.getroot()            # Jeff Added this January 24, 2020     
    root_elem.attrib["xmlns:typens"] = 'http://www.esri.com/schemas/ArcGIS/10.7'
    root_elem.attrib["xmlns:xs"] ='http://www.w3.org/2001/XMLSchema'
    
    # Write the new draft to disk
    with open(newSDdraft, 'w') as f:
        doc.write(f, 'utf-8')
        
        
    # Analyze the service
    try:
        analysis = arcpy.mapping.AnalyzeForSD(newSDdraft)
    except Exception as e:
        errorMessage = 'Error in Analysis.  ' +  e.message
        arcpy.AddError(errorMessage)
     
    if analysis['errors'] == {}:
        # Stage the service
        arcpy.StageService_server(newSDdraft, outputSD)
        print ('Created {}'.format(outputSD))
            
    else:
        # If the sddraft analysis contained errors, display them and quit.
        print (analysis['errors'])
        sys.exit()
   
        
def upload(token, inputUsername, existingItem, finalSD, fileName, title, tags, description): 
    #
    # Overwrite the SD on AGOL with the new SD.
    # This method uses 3rd party module: requests
    #
    
    updateURL = 'https://www.arcgis.com/sharing/rest/content/users/{}/items/{}/update'.format(inputUsername, existingItem)
        
    filesUp = {"file": open(finalSD, 'rb')}
    
    url = updateURL + "?f=json&token="+token+ \
        "&filename="+fileName+ \
        "&type=Service Definition"\
        "&title="+title+ \
        "&tags="+tags+\
        "&description="+description
        
    response = requests.post(url, files=filesUp)     
    itemPartJSON = json.loads(response.text)
    
    if "success" in itemPartJSON:
        itemPartID = itemPartJSON['id']
        print("updated SD:   {}").format(itemPartID)
        return True
    else:
        print (itemPartJSON)
        return False      
    
    
    
def publish(token, inputUsername, itemID):
    #
    # Publish the existing SD on AGOL (it will be turned into a Feature Service)
    #
    
    publishURL = 'https://www.arcgis.com/sharing/rest/content/users/{}/publish'.format(inputUsername)
    
    query_dict = {'itemID': itemID,
              'filetype': 'serviceDefinition',
              'f': 'json',
              'token': token}    
    
    jsonResponse = sendAGOLReq(publishURL, query_dict)
            
    print("successfully updated...{}...").format(jsonResponse['services'])
    

def deleteExisting(token, inputUsername, existingItem):
    #
    # Delete the item from AGOL
    #
        
    deleteURL = 'https://www.arcgis.com/sharing/rest/content/users/{}/items/{}/delete'.format(inputUsername, existingItem)
    
    query_dict = {'f': 'json',
                  'token': token}    
    
    jsonResponse = sendAGOLReq(deleteURL, query_dict)
    
    print("successfully deleted...{}...").format(jsonResponse['itemId'])    

    
def findItem(token, serviceName, user, findType):
    #
    # Find the itemID of whats being updated
    #
    
    searchURL = "https://www.arcgis.com/sharing/rest/search"
    
    query_dict = {'f': 'json',
                  'token': token,
                  'q': "title:\""+ serviceName + "\"AND owner:\"" + user + "\" AND type:\"" + findType + "\""}    
    
    jsonResponse = sendAGOLReq(searchURL, query_dict)
    if (jsonResponse['results']):    
        print('found {} : {}').format(findType, jsonResponse['results'][0]['id'])    
        return jsonResponse['results'][0]["id"]
    else:                                          # Added this if statement in case Item is not found
        print('Did not find {}').format(findType)  # Print not found message.
        return False                               # Return False 

def sendAGOLReq(URL, query_dict):
    #
    #Helper function which takes a URL and a dictionary and sends the request
    #
    
    ## query_string = urllib.urlencode(query_dict)    
    
    jsonResponse = urllib.urlopen(URL, urllib.urlencode(query_dict))
    jsonOuput = json.loads(jsonResponse.read())
    
    if "success" in jsonOuput or "results" in jsonOuput or "services" in jsonOuput:
        return jsonOuput
    else:
        print ("failed:\n")
        print (jsonOuput)
        sys.exit()
        
    
if __name__ == "__main__":
    #
    # start
    #

    # Find and gather settings from the ini file
    localPath = sys.path[0]
    settingsFile = os.path.join(localPath, "settings.ini")

    if os.path.isfile(settingsFile):
        config = ConfigParser.ConfigParser()
        config.read(settingsFile)
    else:
        print ("INI file not found. \nMake sure a valid 'settings.ini' file exists in the same directory as this script.")
        sys.exit()
                
    inputUsername = config.get( 'AGOL', 'USER')
    inputPswd = config.get('AGOL', 'PASS')
                      
    MXD = config.get('FS_INFO', 'MXD')
    serviceName = config.get('FS_INFO', 'SERVICENAME')
    title = config.get('FS_INFO', 'TITLE')    
    tags = config.get('FS_INFO', 'TAGS')
    description = config.get('FS_INFO', 'DESCRIPTION')
    
    
    # create a temp directory under the script     
    tempDir = os.path.join(localPath, "tempDir")
    if not os.path.isdir(tempDir):
        os.mkdir(tempDir)  
    finalSD = os.path.join(tempDir, serviceName + ".sd")  
    
    
    # Turn map document into .SD file for uploading
    makeSD(MXD, serviceName, tempDir, finalSD)
        
    #Get a token
    token = None
    if token is None:  
        token = gentoken(inputUsername, inputPswd)
        
    #Search for the Item and get its ID so it can be updated
    FSitemID = None
    SDitemID = None
    itemID = findItem(token, serviceName, inputUsername, "Feature Service")
    SDitemID = findItem(token, serviceName, inputUsername, "Service Definition")
    print ('ItemID ' + itemID)
    if (not SDitemID):
        print ('SDitemID' + SDitemID)
    else:
        print ('Did not find SDitem')
    
    #overwrite the existing .SD on arcgis.com
    if upload(token, inputUsername, SDitemID, finalSD, serviceName+".sd", title, tags, description):
    
        #delete the existing service
        deleteExisting(token, inputUsername, itemID)
        
        #publish the sd which was just uploaded
        publish(token, inputUsername, SDitemID)
    
    else:
        print (".sd file not uploaded. Check the above errors and try again.")