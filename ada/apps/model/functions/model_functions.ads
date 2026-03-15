with Arthexis.ORM;

package Apps.Model.Functions.Model_Functions is
   --  SQL-callable function registrations for the Model backbone app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register Model backbone SQL functions and helper views.
end Apps.Model.Functions.Model_Functions;
